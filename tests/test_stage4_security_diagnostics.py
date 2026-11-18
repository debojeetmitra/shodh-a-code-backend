import pytest
from app.auth.fixtures import FIXTURE_ORGS, FIXTURE_USERS

pytestmark = pytest.mark.asyncio

async def test_cross_organization_denial(client, auth_headers):
    # Alpha Contest ID
    alpha_contest_id = "c1111111-1111-1111-1111-111111111111"
    alpha_problem_id = "p1111111-1111-1111-1111-111111111111"

    # Beta student tries to access Alpha contest
    res_get = await client.get(
        f"/api/v1/contests/{alpha_contest_id}",
        headers=auth_headers("beta_student_1")
    )
    assert res_get.status_code == 403
    assert res_get.json()["code"] == "CROSS_ORGANIZATION_FORBIDDEN"

    # Beta student tries to join Alpha contest
    res_join = await client.post(
        f"/api/v1/contests/{alpha_contest_id}/join",
        headers=auth_headers("beta_student_1")
    )
    assert res_join.status_code == 403
    assert res_join.json()["code"] == "CROSS_ORGANIZATION_FORBIDDEN"

    # Beta student tries to submit to Alpha contest knowing the problem ID
    res_sub = await client.post(
        f"/api/v1/contests/{alpha_contest_id}/submissions",
        json={"problem_id": alpha_problem_id, "code": "print(1)", "language": "python"},
        headers=auth_headers("beta_student_1")
    )
    assert res_sub.status_code == 403
    assert res_sub.json()["code"] == "CROSS_ORGANIZATION_FORBIDDEN"


async def test_participant_attempting_organizer_actions(client, auth_headers):
    contest_id = "c1111111-1111-1111-1111-111111111111"
    problem_id = "p1111111-1111-1111-1111-111111111111"

    # Participant tries to create contest
    res_create_c = await client.post(
        "/api/v1/contests",
        json={"title": "Hacked Contest", "start_time": "2026-11-18T10:00:00Z", "end_time": "2026-11-18T12:00:00Z"},
        headers=auth_headers("alpha_student_1")
    )
    assert res_create_c.status_code == 403
    assert res_create_c.json()["code"] == "FORBIDDEN"

    # Participant tries to update problem version
    res_pv = await client.post(
        f"/api/v1/problems/{problem_id}/versions",
        json={"statement": "Tampered statement"},
        headers=auth_headers("alpha_student_1")
    )
    assert res_pv.status_code == 403
    assert res_pv.json()["code"] == "FORBIDDEN"

    # Participant tries to trigger rejudge
    res_rej = await client.post(
        f"/api/v1/contests/{contest_id}/submissions/fake-sub-id/rejudge",
        headers=auth_headers("alpha_student_1")
    )
    assert res_rej.status_code == 403
    assert res_rej.json()["code"] == "FORBIDDEN"


async def test_diagnostic_bottleneck_query_plan(client, auth_headers):
    # Organizer queries bottleneck analysis
    res = await client.get("/api/v1/diagnostics/bottleneck", headers=auth_headers("alpha_organizer"))
    assert res.status_code == 200
    data = res.json()
    assert "unindexed_raw_aggregation" in data
    assert "indexed_serving_path" in data
    assert "tradeoff_analysis" in data
    assert "query_plan" in data["unindexed_raw_aggregation"]
    assert "query_plan" in data["indexed_serving_path"]


async def test_structured_audit_logs_and_fault_injection(client, auth_headers):
    # 1. Check audit logs
    log_res = await client.get("/api/v1/diagnostics/audit-logs", headers=auth_headers("alpha_organizer"))
    assert log_res.status_code == 200
    logs = log_res.json()
    assert isinstance(logs, list)

    # 2. Toggle controllable fault injection
    fault_res = await client.post("/api/v1/diagnostics/inject-fault?enable=true", headers=auth_headers("alpha_organizer"))
    assert fault_res.status_code == 200
    assert fault_res.json()["inject_runner_failure"] is True

    # Disable fault injection
    reset_res = await client.post("/api/v1/diagnostics/inject-fault?enable=false", headers=auth_headers("alpha_organizer"))
    assert reset_res.status_code == 200
    assert reset_res.json()["inject_runner_failure"] is False
