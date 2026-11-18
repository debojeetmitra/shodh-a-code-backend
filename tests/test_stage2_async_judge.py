import pytest
from app.auth.fixtures import FIXTURE_ORGS, FIXTURE_USERS
from app.seed.seed_data import SEED_SAMPLE_PROGRAMS
from app.judge.worker import JudgeWorker

pytestmark = pytest.mark.asyncio

async def test_async_submission_persisted_before_judging(client, auth_headers):
    contest_id = "c1111111-1111-1111-1111-111111111111"
    problem_id = "p1111111-1111-1111-1111-111111111111"

    sub_payload = {
        "problem_id": problem_id,
        "code": SEED_SAMPLE_PROGRAMS["SUM2"]["correct"],
        "language": "python"
    }

    # Submit
    res = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json=sub_payload,
        headers=auth_headers("alpha_student_1")
    )
    assert res.status_code == 201
    sub_data = res.json()
    assert sub_data["id"] is not None
    assert sub_data["status"] == "QUEUED"
    assert sub_data["verdict"] == "PENDING"
    assert len(sub_data["attempts"]) == 1
    assert sub_data["attempts"][0]["status"] == "QUEUED"
    assert sub_data["attempts"][0]["attempt_number"] == 1


async def test_verdicts_accepted_and_wrong_answer(client, auth_headers, test_db):
    contest_id = "c1111111-1111-1111-1111-111111111111"
    problem_id = "p1111111-1111-1111-1111-111111111111"

    # 1. Submit ACCEPTED solution
    sub_res_ac = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": problem_id, "code": SEED_SAMPLE_PROGRAMS["SUM2"]["correct"], "language": "python"},
        headers=auth_headers("alpha_student_1")
    )
    sub_id_ac = sub_res_ac.json()["id"]

    # Run worker pass
    worker = JudgeWorker(worker_id="test-worker", runner_mode="SANDBOX_SUBPROCESS", session_factory=test_db)
    processed = await worker.run_once()
    assert processed is True

    # Inspect submission result
    get_res = await client.get(f"/api/v1/submissions/{sub_id_ac}", headers=auth_headers("alpha_student_1"))
    data = get_res.json()
    assert data["status"] == "COMPLETED"
    assert data["verdict"] == "ACCEPTED"
    assert data["execution_mode"] if "execution_mode" in data else data["attempts"][0]["execution_mode"] == "SANDBOX_SUBPROCESS"

    # 2. Submit WRONG_ANSWER solution
    sub_res_wa = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": problem_id, "code": SEED_SAMPLE_PROGRAMS["SUM2"]["wrong_output"], "language": "python"},
        headers=auth_headers("alpha_student_1")
    )
    sub_id_wa = sub_res_wa.json()["id"]

    processed = await worker.run_once()
    assert processed is True

    get_wa_res = await client.get(f"/api/v1/submissions/{sub_id_wa}", headers=auth_headers("alpha_student_1"))
    wa_data = get_wa_res.json()
    assert wa_data["status"] == "COMPLETED"
    assert wa_data["verdict"] == "WRONG_ANSWER"


async def test_verdicts_runtime_and_compilation_and_timeout(client, auth_headers, test_db):
    contest_id = "c1111111-1111-1111-1111-111111111111"
    problem_id = "p1111111-1111-1111-1111-111111111111"
    worker = JudgeWorker(worker_id="test-worker", runner_mode="SANDBOX_SUBPROCESS", session_factory=test_db)

    # Runtime Error
    sub_re = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": problem_id, "code": SEED_SAMPLE_PROGRAMS["SUM2"]["runtime_failure"], "language": "python"},
        headers=auth_headers("alpha_student_1")
    )
    await worker.run_once()
    re_data = (await client.get(f"/api/v1/submissions/{sub_re.json()['id']}", headers=auth_headers("alpha_student_1"))).json()
    assert re_data["verdict"] == "RUNTIME_ERROR"

    # Compilation / Syntax Error
    sub_ce = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": problem_id, "code": "def bad_syntax(:\n  return 0", "language": "python"},
        headers=auth_headers("alpha_student_1")
    )
    await worker.run_once()
    ce_data = (await client.get(f"/api/v1/submissions/{sub_ce.json()['id']}", headers=auth_headers("alpha_student_1"))).json()
    assert ce_data["verdict"] == "COMPILATION_ERROR"

    # Output Limit Exceeded
    sub_ole = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": problem_id, "code": SEED_SAMPLE_PROGRAMS["SUM2"]["excessive_output"], "language": "python"},
        headers=auth_headers("alpha_student_1")
    )
    await worker.run_once()
    ole_data = (await client.get(f"/api/v1/submissions/{sub_ole.json()['id']}", headers=auth_headers("alpha_student_1"))).json()
    assert ole_data["verdict"] == "OUTPUT_LIMIT_EXCEEDED"


async def test_system_error_does_not_penalize_student(client, auth_headers, test_db):
    contest_id = "c1111111-1111-1111-1111-111111111111"
    problem_id = "p1111111-1111-1111-1111-111111111111"
    worker = JudgeWorker(worker_id="test-worker", runner_mode="TEST_DOUBLE", session_factory=test_db)

    # Injected infrastructure failure
    sub_sys = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": problem_id, "code": SEED_SAMPLE_PROGRAMS["SUM2"]["injected_infrastructure_failure"], "language": "python"},
        headers=auth_headers("alpha_student_1")
    )
    sub_id = sub_sys.json()["id"]

    await worker.run_once()

    sys_res = await client.get(f"/api/v1/submissions/{sub_id}", headers=auth_headers("alpha_student_1"))
    assert sys_res.json()["verdict"] == "SYSTEM_ERROR"

    # Now student submits correct answer
    sub_ac = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": problem_id, "code": SEED_SAMPLE_PROGRAMS["SUM2"]["correct"], "language": "python"},
        headers=auth_headers("alpha_student_1")
    )
    await worker.run_once()

    # Check leaderboard: attempts_before_ac MUST BE 0 because SYSTEM_ERROR was not a student fault!
    leaderboard_res = await client.get(f"/api/v1/contests/{contest_id}/leaderboard", headers=auth_headers("alpha_student_1"))
    board = leaderboard_res.json()
    assert len(board["standings"]) > 0
    p1_standing = board["standings"][0]
    p_score = p1_standing["problem_scores"][problem_id]
    assert p_score["is_solved"] is True
    assert p_score["attempts_before_ac"] == 0, "SYSTEM_ERROR should not be counted as a failed penalty attempt!"


async def test_hidden_testcase_data_masking(client, auth_headers, test_db):
    contest_id = "c1111111-1111-1111-1111-111111111111"
    problem_id = "p1111111-1111-1111-1111-111111111111"
    worker = JudgeWorker(worker_id="test-worker", runner_mode="SANDBOX_SUBPROCESS", session_factory=test_db)

    # Submit solution
    sub_res = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": problem_id, "code": SEED_SAMPLE_PROGRAMS["SUM2"]["correct"], "language": "python"},
        headers=auth_headers("alpha_student_1")
    )
    sub_id = sub_res.json()["id"]
    await worker.run_once()

    # 1. Participant inspecting submission details
    part_res = await client.get(f"/api/v1/submissions/{sub_id}", headers=auth_headers("alpha_student_1"))
    part_data = part_res.json()
    attempt = part_data["attempts"][0]

    hidden_results = [tc for tc in attempt["test_case_results"] if tc["is_hidden"]]
    assert len(hidden_results) > 0
    for htc in hidden_results:
        assert htc["stdout"] is None, "Participant must not see hidden stdout!"
        assert htc["stderr"] is None, "Participant must not see hidden stderr!"

    # 2. Organizer inspecting same submission details
    org_res = await client.get(f"/api/v1/submissions/{sub_id}", headers=auth_headers("alpha_organizer"))
    org_data = org_res.json()
    org_attempt = org_data["attempts"][0]
    org_hidden_results = [tc for tc in org_attempt["test_case_results"] if tc["is_hidden"]]
    for htc in org_hidden_results:
        assert htc["stdout"] is not None, "Organizer should have access to full diagnostic stdout."


async def test_private_code_protection(client, auth_headers):
    contest_id = "c1111111-1111-1111-1111-111111111111"
    problem_id = "p1111111-1111-1111-1111-111111111111"

    # Student 1 submits code
    sub_res = await client.post(
        f"/api/v1/contests/{contest_id}/submissions",
        json={"problem_id": problem_id, "code": "secret_solution_code = 123", "language": "python"},
        headers=auth_headers("alpha_student_1")
    )
    sub_id = sub_res.json()["id"]

    # Student 2 tries to read Student 1's submission
    sneak_res = await client.get(f"/api/v1/submissions/{sub_id}", headers=auth_headers("alpha_student_2"))
    assert sneak_res.status_code == 403
    assert sneak_res.json()["code"] == "ACCESS_DENIED"
