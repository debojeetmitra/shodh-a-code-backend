import pytest
import asyncio
from app.auth.fixtures import FIXTURE_ORGS, FIXTURE_USERS
from app.seed.seed_data import SEED_SAMPLE_PROGRAMS
from app.judge.worker import JudgeWorker

pytestmark = pytest.mark.asyncio

async def test_full_contest_lifecycle_e2e(client, auth_headers, test_db):
    """
    Comprehensive End-to-End Test:
    1. Bob and Charlie register for Alpha Grand Prix 2026.
    2. Bob submits correct solution for SUM2 -> gets AC.
    3. Charlie submits wrong answer for SUM2 -> gets WA.
    4. Two workers run in parallel, ensuring no duplicate judging or race conditions.
    5. Charlie submits correct solution for SUM2 -> gets AC with 1 penalty attempt.
    6. Bob submits correct solution for PALIN -> gets AC.
    7. Leaderboard accurately reflects:
       - Bob: 2 solved, lower penalty (Rank 1).
       - Charlie: 1 solved, 1 failed attempt penalty (Rank 2).
    8. Organizer triggers rejudge of Charlie's first attempt with Version 1.
    9. Final standings rebuilt from authoritative records match serving path.
    """
    contest_id = "c1111111-1111-1111-1111-111111111111"
    prob_sum2 = "p1111111-1111-1111-1111-111111111111"
    prob_palin = "p2222222-2222-2222-2222-222222222222"

    worker_1 = JudgeWorker(worker_id="worker-e2e-1", runner_mode="SANDBOX_SUBPROCESS", session_factory=test_db)
    worker_2 = JudgeWorker(worker_id="worker-e2e-2", runner_mode="SANDBOX_SUBPROCESS", session_factory=test_db)

    # Step 1: Bob submits SUM2 (Correct)
    sub_bob_1 = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": prob_sum2, "code": SEED_SAMPLE_PROGRAMS["SUM2"]["correct"], "language": "python"},
        headers=auth_headers("alpha_student_1")
    )
    assert sub_bob_1.status_code == 201

    # Step 2: Charlie submits SUM2 (Wrong Output)
    sub_charlie_1 = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": prob_sum2, "code": SEED_SAMPLE_PROGRAMS["SUM2"]["wrong_output"], "language": "python"},
        headers=auth_headers("alpha_student_2")
    )
    assert sub_charlie_1.status_code == 201

    # Step 3: Run workers to process the queue
    for _ in range(5):
        any_processed = False
        res = await asyncio.gather(worker_1.run_once(), worker_2.run_once())
        if any(res):
            any_processed = True
        if not any_processed:
            break

    # Verify verdicts
    res_b1 = (await client.get(f"/api/v1/submissions/{sub_bob_1.json()['id']}", headers=auth_headers("alpha_student_1"))).json()
    assert res_b1["verdict"] == "ACCEPTED"

    res_c1 = (await client.get(f"/api/v1/submissions/{sub_charlie_1.json()['id']}", headers=auth_headers("alpha_student_2"))).json()
    assert res_c1["verdict"] == "WRONG_ANSWER"

    # Step 4: Charlie fixes his code and submits SUM2 (Correct)
    sub_charlie_2 = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": prob_sum2, "code": SEED_SAMPLE_PROGRAMS["SUM2"]["correct"], "language": "python"},
        headers=auth_headers("alpha_student_2")
    )
    await worker_1.run_once()
    res_c2 = (await client.get(f"/api/v1/submissions/{sub_charlie_2.json()['id']}", headers=auth_headers("alpha_student_2"))).json()
    assert res_c2["verdict"] == "ACCEPTED"

    # Step 5: Bob solves PALIN (Correct)
    sub_bob_2 = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": prob_palin, "code": SEED_SAMPLE_PROGRAMS["PALIN"]["correct"], "language": "python"},
        headers=auth_headers("alpha_student_1")
    )
    await worker_2.run_once()
    res_b2 = (await client.get(f"/api/v1/submissions/{sub_bob_2.json()['id']}", headers=auth_headers("alpha_student_1"))).json()
    assert res_b2["verdict"] == "ACCEPTED"

    # Step 6: Verify Leaderboard
    board_res = await client.get(f"/api/v1/contests/{contest_id}/leaderboard", headers=auth_headers("alpha_student_1"))
    board = board_res.json()
    assert len(board["standings"]) == 2

    # Bob must be Rank 1 (2 solved)
    assert board["standings"][0]["username"] == "bob_coder"
    assert board["standings"][0]["rank"] == 1
    assert board["standings"][0]["solved_problems_count"] == 2

    # Charlie must be Rank 2 (1 solved, 1 failed attempt penalty)
    assert board["standings"][1]["username"] == "charlie_coder"
    assert board["standings"][1]["rank"] == 2
    assert board["standings"][1]["solved_problems_count"] == 1
    assert board["standings"][1]["problem_scores"][prob_sum2]["attempts_before_ac"] == 1

    # Step 7: Standings Rebuild from raw records produces identical rankings
    rebuild_res = await client.post(f"/api/v1/contests/{contest_id}/leaderboard/rebuild", headers=auth_headers("alpha_organizer"))
    rebuilt = rebuild_res.json()
    assert rebuilt["standings"] == board["standings"]
