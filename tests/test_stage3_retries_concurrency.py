import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, update

from app.auth.fixtures import FIXTURE_ORGS, FIXTURE_USERS
from app.seed.seed_data import SEED_SAMPLE_PROGRAMS
from app.judge.worker import JudgeWorker
from app.models import JudgingAttempt, Submission, utcnow

pytestmark = pytest.mark.asyncio

async def test_idempotent_submission_creation(client, auth_headers):
    contest_id = "c1111111-1111-1111-1111-111111111111"
    problem_id = "p1111111-1111-1111-1111-111111111111"
    idempotency_key = "idemp-test-key-12345"

    sub_payload = {
        "problem_id": problem_id,
        "code": SEED_SAMPLE_PROGRAMS["SUM2"]["correct"],
        "language": "python"
    }

    # 1. First request
    res1 = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json=sub_payload,
        headers={**auth_headers("alpha_student_1"), "Idempotency-Key": idempotency_key}
    )
    assert res1.status_code == 201
    sub1_id = res1.json()["id"]

    # 2. Second request with same key and identical payload -> returns same submission
    res2 = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json=sub_payload,
        headers={**auth_headers("alpha_student_1"), "Idempotency-Key": idempotency_key}
    )
    assert res2.status_code in [200, 201]
    assert res2.json()["id"] == sub1_id

    # 3. Third request reusing key with DIFFERENT payload -> 409 Conflict
    conflicting_payload = {
        "problem_id": problem_id,
        "code": "print('DIFFERENT CODE')",
        "language": "python"
    }
    res3 = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json=conflicting_payload,
        headers={**auth_headers("alpha_student_1"), "Idempotency-Key": idempotency_key}
    )
    assert res3.status_code == 409
    assert res3.json()["code"] == "IDEMPOTENCY_CONFLICT"


async def test_competing_workers_no_double_processing(client, auth_headers, test_db):
    contest_id = "c1111111-1111-1111-1111-111111111111"
    problem_id = "p1111111-1111-1111-1111-111111111111"

    # Submit one job
    sub_res = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": problem_id, "code": SEED_SAMPLE_PROGRAMS["SUM2"]["correct"], "language": "python"},
        headers=auth_headers("alpha_student_1")
    )
    sub_id = sub_res.json()["id"]

    worker_a = JudgeWorker(worker_id="worker-A", runner_mode="SANDBOX_SUBPROCESS", session_factory=test_db)
    worker_b = JudgeWorker(worker_id="worker-B", runner_mode="SANDBOX_SUBPROCESS", session_factory=test_db)

    # Both workers compete to claim the job concurrently
    res_a, res_b = await asyncio.gather(
        worker_a.run_once(),
        worker_b.run_once()
    )

    # Exactly one worker must have processed the job; the other found no pending jobs
    assert (res_a is True and res_b is False) or (res_a is False and res_b is True)

    # Verify attempt in DB was only processed once and has one worker id
    async with test_db() as session:
        att = (await session.execute(
            select(JudgingAttempt).where(JudgingAttempt.submission_id == sub_id)
        )).scalar_one()
        assert att.status == "COMPLETED"
        assert att.worker_id in ["worker-A", "worker-B"]


async def test_abandoned_work_recovery_after_lease_expiration(client, auth_headers, test_db):
    contest_id = "c1111111-1111-1111-1111-111111111111"
    problem_id = "p1111111-1111-1111-1111-111111111111"

    sub_res = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": problem_id, "code": SEED_SAMPLE_PROGRAMS["SUM2"]["correct"], "language": "python"},
        headers=auth_headers("alpha_student_1")
    )
    sub_id = sub_res.json()["id"]

    # Simulate worker crashing mid-execution:
    # Set status to RUNNING with lease expired in the past
    past_time = utcnow() - timedelta(seconds=60)
    async with test_db() as session:
        await session.execute(
            update(JudgingAttempt)
            .where(JudgingAttempt.submission_id == sub_id)
            .values(
                status="RUNNING",
                worker_id="crashed-worker",
                lease_expires_at=past_time,
                retry_count=1
            )
        )
        await session.commit()

    # Recovery worker runs
    recovery_worker = JudgeWorker(worker_id="recovery-worker", runner_mode="SANDBOX_SUBPROCESS", session_factory=test_db)
    processed = await recovery_worker.run_once()
    assert processed is True

    # Check that job was recovered and completed
    get_res = await client.get(f"/api/v1/submissions/{sub_id}", headers=auth_headers("alpha_student_1"))
    data = get_res.json()
    assert data["status"] == "COMPLETED"
    assert data["verdict"] == "ACCEPTED"
    assert data["attempts"][0]["worker_id"] == "recovery-worker"
    assert data["attempts"][0]["retry_count"] == 2


async def test_organizer_rejudging_and_standings_consistency(client, auth_headers, test_db):
    contest_id = "c1111111-1111-1111-1111-111111111111"
    problem_id = "p1111111-1111-1111-1111-111111111111"
    worker = JudgeWorker(worker_id="worker-rejudge", runner_mode="SANDBOX_SUBPROCESS", session_factory=test_db)

    # 1. Initial submission: student submits code
    sub_res = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": problem_id, "code": SEED_SAMPLE_PROGRAMS["SUM2"]["correct"], "language": "python"},
        headers=auth_headers("alpha_student_1")
    )
    sub_id = sub_res.json()["id"]
    await worker.run_once()

    sub_after_1 = (await client.get(f"/api/v1/submissions/{sub_id}", headers=auth_headers("alpha_student_1"))).json()
    assert sub_after_1["verdict"] == "ACCEPTED"
    assert len(sub_after_1["attempts"]) == 1
    assert sub_after_1["attempts"][0]["attempt_number"] == 1

    # 2. Organizer triggers rejudge
    rejudge_res = await client.post(
        f"/api/v1/contests/{contest_id}/submissions/{sub_id}/rejudge",
        json={"problem_version": 1},
        headers=auth_headers("alpha_organizer")
    )
    assert rejudge_res.status_code == 200
    rejudge_data = rejudge_res.json()
    assert len(rejudge_data["attempts"]) == 2
    assert rejudge_data["attempts"][0]["is_authoritative"] is False
    assert rejudge_data["attempts"][1]["is_authoritative"] is True
    assert rejudge_data["attempts"][1]["attempt_number"] == 2
    assert rejudge_data["attempts"][1]["status"] == "QUEUED"

    # Worker executes attempt 2
    await worker.run_once()

    sub_after_2 = (await client.get(f"/api/v1/submissions/{sub_id}", headers=auth_headers("alpha_student_1"))).json()
    assert sub_after_2["status"] == "COMPLETED"
    assert sub_after_2["verdict"] == "ACCEPTED"
    assert sub_after_2["active_attempt_id"] == sub_after_2["attempts"][1]["id"]


async def test_leaderboard_rebuild_matches_serving_path(client, auth_headers, test_db):
    contest_id = "c1111111-1111-1111-1111-111111111111"
    problem_id = "p1111111-1111-1111-1111-111111111111"
    worker = JudgeWorker(worker_id="worker-leaderboard", runner_mode="SANDBOX_SUBPROCESS", session_factory=test_db)

    # Bob submits correct solution
    sub_bob = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": problem_id, "code": SEED_SAMPLE_PROGRAMS["SUM2"]["correct"], "language": "python"},
        headers=auth_headers("alpha_student_1")
    )
    await worker.run_once()

    # Charlie submits wrong solution, then correct solution
    sub_charlie_wa = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": problem_id, "code": SEED_SAMPLE_PROGRAMS["SUM2"]["wrong_output"], "language": "python"},
        headers=auth_headers("alpha_student_2")
    )
    await worker.run_once()

    sub_charlie_ac = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": problem_id, "code": SEED_SAMPLE_PROGRAMS["SUM2"]["correct"], "language": "python"},
        headers=auth_headers("alpha_student_2")
    )
    await worker.run_once()

    # 1. Serving path leaderboard
    serving_res = await client.get(f"/api/v1/contests/{contest_id}/leaderboard", headers=auth_headers("alpha_student_1"))
    serving_data = serving_res.json()

    # 2. Rebuild leaderboard from raw authoritative records
    rebuild_res = await client.post(f"/api/v1/contests/{contest_id}/leaderboard/rebuild", headers=auth_headers("alpha_organizer"))
    rebuild_data = rebuild_res.json()

    # Both must match exactly
    assert len(serving_data["standings"]) == len(rebuild_data["standings"])
    for s_row, r_row in zip(serving_data["standings"], rebuild_data["standings"]):
        assert s_row["user_id"] == r_row["user_id"]
        assert s_row["rank"] == r_row["rank"]
        assert s_row["solved_problems_count"] == r_row["solved_problems_count"]
        assert s_row["total_penalty_minutes"] == r_row["total_penalty_minutes"]
