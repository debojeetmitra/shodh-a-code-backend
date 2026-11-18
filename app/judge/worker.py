import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, and_, or_

from app.database import AsyncSessionLocal
from app.config import settings
from app.models import (
    Submission, JudgingAttempt, ProblemVersion, TestCase,
    JudgingTestCaseResult, Contest, AuditLog, utcnow
)
from app.judge import get_runner
from app.judge.runner_interface import Verdict, TestCaseExecutionResult
from app.judge.scorer import refresh_standings_cache

logger = logging.getLogger("judge.worker")

class JudgeWorker:
    def __init__(
        self,
        worker_id: Optional[str] = None,
        lease_seconds: int = settings.WORKER_LEASE_SECONDS,
        max_retries: int = settings.WORKER_MAX_RETRIES,
        runner_mode: Optional[str] = None,
        session_factory=None
    ):
        self.worker_id = worker_id or settings.WORKER_ID
        self.lease_seconds = lease_seconds
        self.max_retries = max_retries
        self.runner_mode = runner_mode
        self.session_factory = session_factory or AsyncSessionLocal
        self.running = False

    async def claim_next_job(self, db: AsyncSession) -> Optional[JudgingAttempt]:
        """
        Atomically claims a queued or abandoned judging attempt using optimistic / pessimistic locking.
        Recovers work where worker lease expired.
        """
        now = utcnow()
        
        # Build candidate query: QUEUED or abandoned RUNNING with expired lease
        stmt = (
            select(JudgingAttempt)
            .where(
                or_(
                    JudgingAttempt.status == "QUEUED",
                    and_(
                        JudgingAttempt.status == "RUNNING",
                        JudgingAttempt.lease_expires_at < now
                    )
                )
            )
            .order_by(JudgingAttempt.created_at.asc())
            .limit(1)
        )

        # For postgres, with_for_update(skip_locked=True) is used
        if not settings.DATABASE_URL.startswith("sqlite"):
            stmt = stmt.with_for_update(skip_locked=True)

        res = await db.execute(stmt)
        attempt = res.scalar_one_or_none()
        if not attempt:
            return None

        # Check retry limit for abandoned attempts
        if attempt.retry_count >= self.max_retries:
            attempt.status = "FAILED"
            attempt.verdict = Verdict.SYSTEM_ERROR
            attempt.error_details = f"Exceeded max retries ({self.max_retries}) due to runner crash or timeout"
            attempt.finished_at = now

            # Update parent submission
            sub_res = await db.execute(select(Submission).where(Submission.id == attempt.submission_id))
            sub = sub_res.scalar_one_or_none()
            if sub and attempt.is_authoritative:
                sub.status = "FAILED"
                sub.verdict = Verdict.SYSTEM_ERROR

            await db.commit()
            return None

        # Atomically claim using compare-and-swap
        claim_stmt = (
            update(JudgingAttempt)
            .where(
                JudgingAttempt.id == attempt.id,
                JudgingAttempt.status == attempt.status
            )
            .values(
                status="RUNNING",
                worker_id=self.worker_id,
                started_at=now,
                lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                retry_count=JudgingAttempt.retry_count + 1
            )
        )
        claim_res = await db.execute(claim_stmt)
        if claim_res.rowcount == 0:
            # Another worker already claimed this attempt!
            await db.rollback()
            return None

        # Also update submission status
        await db.execute(
            update(Submission)
            .where(Submission.id == attempt.submission_id, Submission.status == "QUEUED")
            .values(status="RUNNING")
        )

        await db.commit()
        await db.refresh(attempt)
        return attempt

    async def process_attempt(self, attempt_id: str, db: AsyncSession) -> bool:
        """
        Executes an attempt against its versioned test suite, records per-test-case results,
        evaluates overall verdict, and updates standings if authoritative.
        """
        res = await db.execute(
            select(JudgingAttempt).where(JudgingAttempt.id == attempt_id)
        )
        attempt = res.scalar_one_or_none()
        if not attempt:
            return False

        # Load submission
        sub_res = await db.execute(
            select(Submission).where(Submission.id == attempt.submission_id)
        )
        submission = sub_res.scalar_one_or_none()
        if not submission:
            return False

        # Load problem version and test cases
        pv_res = await db.execute(
            select(ProblemVersion).where(ProblemVersion.id == attempt.problem_version_id)
        )
        problem_version = pv_res.scalar_one_or_none()
        if not problem_version:
            attempt.status = "FAILED"
            attempt.verdict = Verdict.SYSTEM_ERROR
            attempt.error_details = "Referenced ProblemVersion not found"
            await db.commit()
            return False

        tc_res = await db.execute(
            select(TestCase)
            .where(
                and_(
                    TestCase.problem_version_id == attempt.problem_version_id,
                    TestCase.test_suite_version == attempt.test_suite_version
                )
            )
            .order_by(TestCase.order_index.asc())
        )
        test_cases = tc_res.scalars().all()

        runner = get_runner(self.runner_mode)
        attempt.execution_mode = runner.execution_mode

        # Structured audit log of execution start
        audit_start = AuditLog(
            request_id=None,
            submission_id=submission.id,
            attempt_id=attempt.id,
            actor_id=submission.user_id,
            action="JUDGING_STARTED",
            details=f"Worker {self.worker_id} started judging attempt {attempt.attempt_number} in mode {attempt.execution_mode}"
        )
        db.add(audit_start)
        await db.commit()

        # Delete any partial test results from previous crashed attempt
        existing_tc_results = (await db.execute(
            select(JudgingTestCaseResult).where(JudgingTestCaseResult.attempt_id == attempt.id)
        )).scalars().all()
        for old_tc in existing_tc_results:
            await db.delete(old_tc)
        await db.commit()

        # Execute test cases
        final_verdict = Verdict.ACCEPTED
        max_time_ms = 0
        max_mem_kb = 0
        first_failure_reason = None

        # Check for simulated worker crash failpoint
        if "SIMULATE_WORKER_CRASH_BEFORE_COMMIT" in submission.code:
            logger.warning(f"Injected crash failpoint triggered for submission {submission.id} before commit!")
            raise RuntimeError("Injected Worker Crash Failure!")

        for idx, tc in enumerate(test_cases, start=1):
            exec_res: TestCaseExecutionResult = await runner.execute(
                code=submission.code,
                input_data=tc.input_data,
                expected_output=tc.expected_output,
                time_limit_ms=problem_version.time_limit_ms,
                memory_limit_mb=problem_version.memory_limit_mb
            )

            max_time_ms = max(max_time_ms, exec_res.execution_time_ms)
            max_mem_kb = max(max_mem_kb, exec_res.memory_used_kb)

            tc_result = JudgingTestCaseResult(
                attempt_id=attempt.id,
                test_case_id=tc.id,
                test_case_index=idx,
                is_hidden=tc.is_hidden,
                verdict=exec_res.verdict.value if hasattr(exec_res.verdict, "value") else str(exec_res.verdict),
                execution_time_ms=exec_res.execution_time_ms,
                memory_used_kb=exec_res.memory_used_kb,
                stdout_truncated=exec_res.stdout[:500],
                stderr_truncated=exec_res.stderr[:500]
            )
            db.add(tc_result)

            # Determine first non-accepted verdict
            if final_verdict == Verdict.ACCEPTED and exec_res.verdict != Verdict.ACCEPTED:
                final_verdict = exec_res.verdict
                first_failure_reason = exec_res.error_message or exec_res.stderr[:300]

        now = utcnow()
        attempt.status = "COMPLETED"
        attempt.worker_id = self.worker_id
        attempt.verdict = final_verdict.value if hasattr(final_verdict, "value") else str(final_verdict)
        attempt.execution_time_ms = max_time_ms
        attempt.memory_used_kb = max_mem_kb
        attempt.finished_at = now
        attempt.error_details = first_failure_reason

        # Check if attempt is still authoritative in persistent storage
        auth_check = (await db.execute(
            select(JudgingAttempt.is_authoritative).where(JudgingAttempt.id == attempt.id)
        )).scalar_one_or_none()

        if auth_check:
            attempt.is_authoritative = True
            await db.execute(
                update(Submission)
                .where(Submission.id == attempt.submission_id)
                .values(
                    status="COMPLETED",
                    verdict=attempt.verdict,
                    execution_time_ms=max_time_ms,
                    memory_used_kb=max_mem_kb,
                    active_attempt_id=attempt.id,
                    updated_at=now
                )
            )
            await db.commit()
            # Update leaderboard cache
            await refresh_standings_cache(submission.contest_id, db)
        else:
            # Attempt was superseded by an organizer rejudge!
            attempt.is_authoritative = False
            logger.info(f"Attempt {attempt.id} was superseded. Not updating standings.")
            await db.commit()

        # Audit log completion
        audit_end = AuditLog(
            request_id=None,
            submission_id=submission.id,
            attempt_id=attempt.id,
            actor_id=submission.user_id,
            action="JUDGING_COMPLETED",
            details=f"Verdict: {attempt.verdict}, Time: {max_time_ms}ms, Mem: {max_mem_kb}KB, Authoritative: {attempt.is_authoritative}"
        )
        db.add(audit_end)
        await db.commit()

        return True

    async def run_once(self, db: Optional[AsyncSession] = None) -> bool:
        """Runs a single poll-and-process iteration. Returns True if a job was processed."""
        if db is not None:
            attempt = await self.claim_next_job(db)
            if not attempt:
                return False
            await self.process_attempt(attempt.id, db)
            return True

        async with self.session_factory() as session:
            try:
                attempt = await self.claim_next_job(session)
                if not attempt:
                    return False
                await self.process_attempt(attempt.id, session)
                return True
            except Exception as e:
                logger.error(f"Worker {self.worker_id} error processing job: {e}", exc_info=True)
                await session.rollback()
                return False

    async def run_loop(self):
        """Continuous worker execution loop."""
        self.running = True
        logger.info(f"Starting worker loop for {self.worker_id}...")
        while self.running:
            processed = await self.run_once()
            if not processed:
                await asyncio.sleep(settings.WORKER_POLL_INTERVAL_SEC)
