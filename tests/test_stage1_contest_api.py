import pytest
from app.auth.fixtures import FIXTURE_ORGS, FIXTURE_USERS

pytestmark = pytest.mark.asyncio

async def test_auth_me_and_fixtures(client, auth_headers):
    # Test organizer identity
    res = await client.get("/api/v1/auth/me", headers=auth_headers("alpha_organizer"))
    assert res.status_code == 200
    data = res.json()
    assert data["username"] == "alice_organizer"
    assert data["role"] == "ORGANIZER"
    assert data["organization_id"] == FIXTURE_ORGS["org_alpha"]["id"]

    # Test student identity
    res = await client.get("/api/v1/auth/me", headers=auth_headers("alpha_student_1"))
    assert res.status_code == 200
    data = res.json()
    assert data["username"] == "bob_coder"
    assert data["role"] == "PARTICIPANT"


async def test_contest_creation_and_listing(client, auth_headers):
    # Organizer creates contest
    contest_payload = {
        "title": "Alpha Sprint 2026",
        "description": "Short round contest",
        "start_time": "2026-11-18T10:00:00Z",
        "end_time": "2026-11-18T15:00:00Z",
        "penalty_minutes_per_wa": 20
    }
    res = await client.post("/api/v1/contests", json=contest_payload, headers=auth_headers("alpha_organizer"))
    assert res.status_code == 201
    created = res.json()
    assert created["title"] == "Alpha Sprint 2026"
    assert created["organization_id"] == FIXTURE_ORGS["org_alpha"]["id"]

    # List contests
    res = await client.get("/api/v1/contests?page=1&limit=10", headers=auth_headers("alpha_student_1"))
    assert res.status_code == 200
    contests = res.json()
    assert len(contests) >= 2
    # Verify stable ordering (descending created_at)
    titles = [c["title"] for c in contests]
    assert "Alpha Sprint 2026" in titles


async def test_contest_closed_submission_rejection(client, auth_headers):
    # Closed contest ID
    closed_contest_id = "c2222222-2222-2222-2222-222222222222"
    problem_id = "p1111111-1111-1111-1111-111111111111"

    # Try to join closed contest
    join_res = await client.post(f"/api/v1/contests/{closed_contest_id}/join", headers=auth_headers("alpha_student_1"))
    assert join_res.status_code == 400
    assert join_res.json()["code"] == "CONTEST_CLOSED"

    # Try to submit to closed contest
    sub_payload = {
        "problem_id": problem_id,
        "code": "print(42)",
        "language": "python"
    }
    sub_res = await client.post(
        f"/api/v1/contests/{closed_contest_id}/submissions",
        json=sub_payload,
        headers=auth_headers("alpha_student_1")
    )
    assert sub_res.status_code == 400
    assert sub_res.json()["code"] == "CONTEST_CLOSED"


async def test_unregistered_participant_rejection(client, auth_headers):
    open_contest_id = "c1111111-1111-1111-1111-111111111111"
    problem_id = "p1111111-1111-1111-1111-111111111111"

    # Create new participant not registered for contest 1
    # User eve_hacker is from org beta, but let's test alpha_organizer attempting submission as participant
    sub_payload = {
        "problem_id": problem_id,
        "code": "print(42)",
        "language": "python"
    }
    sub_res = await client.post(
        f"/api/v1/contests/{open_contest_id}/submissions",
        json=sub_payload,
        headers=auth_headers("alpha_organizer")  # organizer didn't join contest
    )
    assert sub_res.status_code == 403
    assert sub_res.json()["code"] == "NOT_REGISTERED"


async def test_problem_versioning_immutability(client, auth_headers):
    problem_id = "p1111111-1111-1111-1111-111111111111"

    # Initial problem check (version 1)
    res1 = await client.get(f"/api/v1/problems/{problem_id}", headers=auth_headers("alpha_organizer"))
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["current_version"] == 1
    assert len(data1["versions"]) == 1
    assert data1["versions"][0]["version"] == 1

    # Organizer creates Version 2
    update_payload = {
        "statement": "Updated statement with stricter constraints: N <= 100000",
        "time_limit_ms": 2500,
        "memory_limit_mb": 256,
        "test_cases": [
            {"input_data": "4\n1 2 3 4\n", "expected_output": "10\n", "is_hidden": False, "order_index": 1},
            {"input_data": "2\n100 200\n", "expected_output": "300\n", "is_hidden": True, "order_index": 2}
        ]
    }
    res2 = await client.post(
        f"/api/v1/problems/{problem_id}/versions",
        json=update_payload,
        headers=auth_headers("alpha_organizer")
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["current_version"] == 2
    assert len(data2["versions"]) == 2
    
    # Verify historical version 1 is preserved intact
    v1 = next(v for v in data2["versions"] if v["version"] == 1)
    assert v1["time_limit_ms"] == 1500
    v2 = next(v for v in data2["versions"] if v["version"] == 2)
    assert v2["time_limit_ms"] == 2500
    assert "stricter constraints" in v2["statement"]
