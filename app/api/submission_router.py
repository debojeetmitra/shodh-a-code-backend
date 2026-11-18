import hashlib
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func

from app.database import get_db
from app.models import (
    Submission, JudgingAttempt, Problem, ProblemVersion,
    Contest, ContestParticipant, User, JudgingTestCaseResult,
    AuditLog, utcnow, ensure_utc
)
from app.schemas import (
    SubmissionCreate, SubmissionResponse, JudgingAttemptResponse,
    TestCaseResultResponse, RejudgeRequest
)
from app.auth.dependencies import get_current_user, require_role, verify_organization

router = APIRouter(tags=["Submissions & Judging"])

def compute_request_hash(problem_id: str, language: str, code: str) -> str:
    content = f"{problem_id}:{language}:{code.strip()}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

@router.post(
    "/contests/{contest_id}/submissions",
    response_model=SubmissionResponse,
    status_code=status.HTTP_201_CREATED
)
async def create_submission(
    contest_id: str,
    sub_in: SubmissionCreate,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Accepts, persists, and enqueues a submission returning an identifier before judging finishes.
    Enforces contest open status, participant eligibility, and strict idempotency handling.
    """
    # 1. Fetch contest and verify org boundary
    contest = (await db.execute(select(Contest).where(Contest.id == contest_id))).scalar_one_or_none()
    if not contest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CONTEST_NOT_FOUND", "message": f"Contest {contest_id} not found."}
        )
    verify_organization(current_user, contest.organization_id)

    # 2. Check contest opening/closing behavior
    now = utcnow()
    start_utc = ensure_utc(contest.start_time)
    end_utc = ensure_utc(contest.end_time)
    if now < start_utc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "CONTEST_NOT_STARTED", "message": "Contest has not started yet."}
        )
    if now > end_utc or contest.status == "CLOSED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "CONTEST_CLOSED", "message": "Contest has ended. Submissions are closed."}
        )

    # 3. Check participant eligibility
    part_res = await db.execute(
        select(ContestParticipant).where(
            and_(
                ContestParticipant.contest_id == contest_id,
                ContestParticipant.user_id == current_user.id
            )
        )
    )
    participant = part_res.scalar_one_or_none()
    if not participant or not participant.is_eligible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "NOT_REGISTERED", "message": "User is not registered or eligible for this contest."}
        )

    # 4. Check problem exists and belongs to contest org
    prob = (await db.execute(select(Problem).where(Problem.id == sub_in.problem_id))).scalar_one_or_none()
    if not prob or prob.organization_id != contest.organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROBLEM_NOT_FOUND", "message": f"Problem {sub_in.problem_id} not found in this contest."}
        )

    request_hash = compute_request_hash(sub_in.problem_id, sub_in.language, sub_in.code)

    # 5. Strict Idempotency Handling
    if idempotency_key:
        existing_sub = (await db.execute(
            select(Submission).where(
                and_(
                    Submission.contest_id == contest_id,
                    Submission.user_id == current_user.id,
                    Submission.idempotency_key == idempotency_key
                )
            )
        )).scalar_one_or_none()

        if existing_sub:
            if existing_sub.request_hash == request_hash:
                # Same key and equivalent request from same actor -> return same logical submission
                return await get_submission_response_model(existing_sub.id, current_user, db)
            else:
                # Reusing a key with different code or parameters -> explicit conflict
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "IDEMPOTENCY_CONFLICT",
                        "message": "Idempotency key has already been used with different submission parameters."
                    }
                )

    # 6. Retrieve active problem version
    pv = (await db.execute(
        select(ProblemVersion).where(
            and_(
                ProblemVersion.problem_id == prob.id,
                ProblemVersion.version == prob.current_version
            )
        )
    )).scalar_one_or_none()
    if not pv:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "PROBLEM_VERSION_MISSING", "message": "Active problem version is missing."}
        )

    # 7. Atomic Transaction: Persist Submission + Initial Attempt (Transactional Outbox)
    submission = Submission(
        contest_id=contest_id,
        problem_id=sub_in.problem_id,
        user_id=current_user.id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        code=sub_in.code,
        language=sub_in.language,
        problem_version_id=pv.id,
        test_suite_version=1,
        status="QUEUED",
        verdict="PENDING"
    )
    db.add(submission)
    await db.flush()

    attempt = JudgingAttempt(
        submission_id=submission.id,
        attempt_number=1,
        problem_version_id=pv.id,
        test_suite_version=1,
        status="QUEUED",
        verdict="PENDING",
        is_authoritative=True
    )
    db.add(attempt)
    await db.flush()

    submission.active_attempt_id = attempt.id

    # Audit log
    db.add(AuditLog(
        request_id=None,
        submission_id=submission.id,
        attempt_id=attempt.id,
        actor_id=current_user.id,
        action="SUBMISSION_CREATED",
        details=f"Created submission with attempt 1 (idempotency_key={idempotency_key})"
    ))

    await db.commit()
    await db.refresh(submission)

    return await get_submission_response_model(submission.id, current_user, db)


@router.get("/contests/{contest_id}/submissions", response_model=List[SubmissionResponse])
async def list_contest_submissions(
    contest_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Lists submissions for a contest with pagination and stable ordering.
    Participants can only view their own submissions. Organizers can view all.
    """
    contest = (await db.execute(select(Contest).where(Contest.id == contest_id))).scalar_one_or_none()
    if not contest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CONTEST_NOT_FOUND", "message": f"Contest {contest_id} not found."}
        )
    verify_organization(current_user, contest.organization_id)

    offset = (page - 1) * limit
    stmt = select(Submission).where(Submission.contest_id == contest_id)
    if current_user.role == "PARTICIPANT":
        stmt = stmt.where(Submission.user_id == current_user.id)

    stmt = stmt.order_by(Submission.created_at.desc(), Submission.id.asc()).offset(offset).limit(limit)
    subs = (await db.execute(stmt)).scalars().all()

    return [await get_submission_response_model(s.id, current_user, db) for s in subs]


@router.get("/submissions/{submission_id}", response_model=SubmissionResponse)
async def get_submission(
    submission_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves full submission details, attempts history, and test case results.
    Enforces privacy boundaries: participants can only inspect their own submissions,
    and hidden test inputs/outputs are masked.
    """
    return await get_submission_response_model(submission_id, current_user, db)


@router.post(
    "/contests/{contest_id}/submissions/{submission_id}/rejudge",
    response_model=SubmissionResponse
)
async def rejudge_submission(
    contest_id: str,
    submission_id: str,
    rejudge_in: Optional[RejudgeRequest] = None,
    current_user: User = Depends(require_role(["ORGANIZER"])),
    db: AsyncSession = Depends(get_db)
):
    """
    Allows an organizer to rejudge a submission against an explicitly selected problem and test-suite version.
    Preserves all previous attempts and establishes the new attempt as authoritative.
    """
    sub = (await db.execute(
        select(Submission).where(
            and_(
                Submission.id == submission_id,
                Submission.contest_id == contest_id
            )
        )
    )).scalar_one_or_none()
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SUBMISSION_NOT_FOUND", "message": f"Submission {submission_id} not found in contest {contest_id}."}
        )

    # Check org boundary
    contest = (await db.execute(select(Contest).where(Contest.id == contest_id))).scalar_one_or_none()
    verify_organization(current_user, contest.organization_id)

    # Determine problem version
    if rejudge_in and rejudge_in.problem_version:
        target_version = rejudge_in.problem_version
    else:
        # Use latest version of the problem
        prob = (await db.execute(select(Problem).where(Problem.id == sub.problem_id))).scalar_one()
        target_version = prob.current_version

    pv = (await db.execute(
        select(ProblemVersion).where(
            and_(
                ProblemVersion.problem_id == sub.problem_id,
                ProblemVersion.version == target_version
            )
        )
    )).scalar_one_or_none()
    if not pv:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_PROBLEM_VERSION", "message": f"Problem version {target_version} does not exist."}
        )

    test_suite_v = (rejudge_in.test_suite_version if rejudge_in and rejudge_in.test_suite_version else 1)

    # Mark all existing attempts as non-authoritative
    existing_attempts = (await db.execute(
        select(JudgingAttempt).where(JudgingAttempt.submission_id == sub.id)
    )).scalars().all()

    for att in existing_attempts:
        att.is_authoritative = False

    max_attempt_num = max([att.attempt_number for att in existing_attempts], default=0)
    new_attempt_num = max_attempt_num + 1

    # Create new authoritative attempt
    new_attempt = JudgingAttempt(
        submission_id=sub.id,
        attempt_number=new_attempt_num,
        problem_version_id=pv.id,
        test_suite_version=test_suite_v,
        status="QUEUED",
        verdict="PENDING",
        is_authoritative=True
    )
    db.add(new_attempt)
    await db.flush()

    sub.status = "QUEUED"
    sub.verdict = "PENDING"
    sub.active_attempt_id = new_attempt.id
    sub.problem_version_id = pv.id
    sub.test_suite_version = test_suite_v
    sub.updated_at = utcnow()

    db.add(AuditLog(
        request_id=None,
        submission_id=sub.id,
        attempt_id=new_attempt.id,
        actor_id=current_user.id,
        action="REJUDGE_REQUESTED",
        details=f"Rejudge triggered: new attempt {new_attempt_num} for problem version {target_version}"
    ))

    await db.commit()
    await db.refresh(sub)
    return await get_submission_response_model(sub.id, current_user, db)


async def get_submission_response_model(
    submission_id: str,
    current_user: User,
    db: AsyncSession
) -> SubmissionResponse:
    sub = (await db.execute(select(Submission).where(Submission.id == submission_id))).scalar_one_or_none()
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SUBMISSION_NOT_FOUND", "message": f"Submission {submission_id} not found."}
        )

    # Privacy check: if participant, they can only view their own submission
    if current_user.role == "PARTICIPANT" and sub.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ACCESS_DENIED", "message": "Cannot view private submissions of other participants."}
        )

    # Also verify org match
    contest = (await db.execute(select(Contest).where(Contest.id == sub.contest_id))).scalar_one()
    verify_organization(current_user, contest.organization_id)

    # Fetch attempts
    attempts = (await db.execute(
        select(JudgingAttempt)
        .where(JudgingAttempt.submission_id == sub.id)
        .order_by(JudgingAttempt.attempt_number.asc())
    )).scalars().all()

    attempt_responses = []
    for att in attempts:
        tc_results = (await db.execute(
            select(JudgingTestCaseResult)
            .where(JudgingTestCaseResult.attempt_id == att.id)
            .order_by(JudgingTestCaseResult.test_case_index.asc())
        )).scalars().all()

        tc_resp_list = []
        for tc_res in tc_results:
            is_hidden = tc_res.is_hidden
            # Mask stdout and stderr for hidden test cases if participant
            stdout_val = tc_res.stdout_truncated if (not is_hidden or current_user.role == "ORGANIZER") else None
            stderr_val = tc_res.stderr_truncated if (not is_hidden or current_user.role == "ORGANIZER") else None

            tc_resp_list.append(TestCaseResultResponse(
                test_case_index=tc_res.test_case_index,
                is_hidden=is_hidden,
                verdict=tc_res.verdict,
                execution_time_ms=tc_res.execution_time_ms,
                memory_used_kb=tc_res.memory_used_kb,
                stdout=stdout_val,
                stderr=stderr_val
            ))

        attempt_responses.append(JudgingAttemptResponse(
            id=att.id,
            attempt_number=att.attempt_number,
            problem_version_id=att.problem_version_id,
            test_suite_version=att.test_suite_version,
            worker_id=att.worker_id,
            retry_count=att.retry_count,
            status=att.status,
            verdict=att.verdict,
            execution_time_ms=att.execution_time_ms,
            memory_used_kb=att.memory_used_kb,
            error_details=att.error_details,
            execution_mode=att.execution_mode,
            is_authoritative=att.is_authoritative,
            created_at=att.created_at,
            started_at=att.started_at,
            finished_at=att.finished_at,
            test_case_results=tc_resp_list
        ))

    return SubmissionResponse(
        id=sub.id,
        contest_id=sub.contest_id,
        problem_id=sub.problem_id,
        user_id=sub.user_id,
        idempotency_key=sub.idempotency_key,
        language=sub.language,
        code=sub.code if (current_user.role == "ORGANIZER" or current_user.id == sub.user_id) else None,
        problem_version_id=sub.problem_version_id,
        test_suite_version=sub.test_suite_version,
        status=sub.status,
        verdict=sub.verdict,
        execution_time_ms=sub.execution_time_ms,
        memory_used_kb=sub.memory_used_kb,
        active_attempt_id=sub.active_attempt_id,
        created_at=sub.created_at,
        updated_at=sub.updated_at,
        attempts=attempt_responses
    )
