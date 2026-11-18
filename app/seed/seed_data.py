import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import AsyncSessionLocal, init_db
from app.models import (
    Organization, User, Contest, ContestParticipant,
    Problem, ProblemVersion, TestCase, utcnow
)
from app.auth.fixtures import FIXTURE_ORGS, FIXTURE_USERS

SEED_SAMPLE_PROGRAMS = {
    "SUM2": {
        "correct": (
            "import sys\n"
            "lines = sys.stdin.read().splitlines()\n"
            "if lines:\n"
            "    arr = list(map(int, lines[1].split()))\n"
            "    print(sum(arr))\n"
        ),
        "wrong_output": (
            "print(999999)\n"
        ),
        "runtime_failure": (
            "import sys\n"
            "raise RuntimeError('Deliberate student runtime exception')\n"
        ),
        "timeout": (
            "import time\n"
            "time.sleep(10)\n"
            "print(0)\n"
        ),
        "excessive_output": (
            "print('X' * 200000)\n"
        ),
        "injected_infrastructure_failure": (
            "# CRASH_INFRASTRUCTURE\n"
            "print('Crash runner')\n"
        )
    },
    "PALIN": {
        "correct": (
            "import sys\n"
            "s = sys.stdin.read().strip()\n"
            "print('YES' if s == s[::-1] else 'NO')\n"
        ),
        "wrong_output": (
            "print('WRONG')\n"
        ),
        "runtime_failure": (
            "1 / 0\n"
        ),
        "timeout": (
            "while True:\n"
            "    pass\n"
        ),
        "excessive_output": (
            "while True:\n"
            "    print('SPAM'*1000)\n"
        )
    },
    "MAXSUB": {
        "correct": (
            "import sys\n"
            "nums = list(map(int, sys.stdin.read().split()))\n"
            "max_so_far = nums[0]\n"
            "curr_max = nums[0]\n"
            "for x in nums[1:]:\n"
            "    curr_max = max(x, curr_max + x)\n"
            "    max_so_far = max(max_so_far, curr_max)\n"
            "print(max_so_far)\n"
        )
    }
}

async def seed_database(db: AsyncSession):
    """Deterministically populates organizations, users, contests, problems, and test cases."""
    now = utcnow()

    # 1. Organizations
    org_map = {}
    for key, org_data in FIXTURE_ORGS.items():
        existing = (await db.execute(select(Organization).where(Organization.id == org_data["id"]))).scalar_one_or_none()
        if not existing:
            org = Organization(
                id=org_data["id"],
                name=org_data["name"],
                slug=org_data["slug"],
                created_at=now - timedelta(days=10)
            )
            db.add(org)
            org_map[key] = org
        else:
            org_map[key] = existing
    await db.flush()

    # 2. Users
    user_map = {}
    for key, udata in FIXTURE_USERS.items():
        existing = (await db.execute(select(User).where(User.id == udata["id"]))).scalar_one_or_none()
        if not existing:
            u = User(
                id=udata["id"],
                organization_id=udata["organization_id"],
                username=udata["username"],
                email=udata["email"],
                role=udata["role"],
                api_key=udata["api_key"],
                created_at=now - timedelta(days=5)
            )
            db.add(u)
            user_map[key] = u
        else:
            user_map[key] = existing
    await db.flush()

    # 3. Contests
    contest1_id = "c1111111-1111-1111-1111-111111111111"
    c1 = (await db.execute(select(Contest).where(Contest.id == contest1_id))).scalar_one_or_none()
    if not c1:
        c1 = Contest(
            id=contest1_id,
            organization_id=FIXTURE_ORGS["org_alpha"]["id"],
            title="Alpha Grand Prix 2026",
            description="Official collegiate programming contest of Alpha Tech.",
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=3),
            status="OPEN",
            penalty_minutes_per_wa=20,
            created_at=now - timedelta(days=1)
        )
        db.add(c1)

    contest2_id = "c2222222-2222-2222-2222-222222222222"
    c2 = (await db.execute(select(Contest).where(Contest.id == contest2_id))).scalar_one_or_none()
    if not c2:
        c2 = Contest(
            id=contest2_id,
            organization_id=FIXTURE_ORGS["org_alpha"]["id"],
            title="Alpha Winter Cup 2025 (Archived)",
            description="Past contest for archival queries and historical standings.",
            start_time=now - timedelta(days=5),
            end_time=now - timedelta(days=4),
            status="CLOSED",
            penalty_minutes_per_wa=20,
            created_at=now - timedelta(days=6)
        )
        db.add(c2)

    contest3_id = "c3333333-3333-3333-3333-333333333333"
    c3 = (await db.execute(select(Contest).where(Contest.id == contest3_id))).scalar_one_or_none()
    if not c3:
        c3 = Contest(
            id=contest3_id,
            organization_id=FIXTURE_ORGS["org_beta"]["id"],
            title="Beta Championship 2026",
            description="Beta University internal contest.",
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=4),
            status="OPEN",
            penalty_minutes_per_wa=20,
            created_at=now - timedelta(days=1)
        )
        db.add(c3)
    await db.flush()

    # 4. Registrations
    for contest_id, user_key in [
        (contest1_id, "alpha_student_1"),
        (contest1_id, "alpha_student_2"),
        (contest3_id, "beta_student_1"),
    ]:
        u = FIXTURE_USERS[user_key]
        reg_existing = (await db.execute(
            select(ContestParticipant).where(
                ContestParticipant.contest_id == contest_id,
                ContestParticipant.user_id == u["id"]
            )
        )).scalar_one_or_none()
        if not reg_existing:
            reg = ContestParticipant(
                contest_id=contest_id,
                user_id=u["id"],
                registered_at=now - timedelta(minutes=45),
                is_eligible=True
            )
            db.add(reg)
    await db.flush()

    # 5. Problems & Test Cases (Alpha Org)
    problems_def = [
        {
            "id": "p1111111-1111-1111-1111-111111111111",
            "org_id": FIXTURE_ORGS["org_alpha"]["id"],
            "code": "SUM2",
            "title": "Sum of Array",
            "statement": "Given an array of N integers, print their sum.\nInput: Line 1: N, Line 2: N space-separated integers.\nOutput: Single integer representing sum.",
            "time_limit_ms": 1500,
            "memory_limit_mb": 128,
            "test_cases": [
                {"input": "3\n1 2 3\n", "output": "6\n", "is_hidden": False},
                {"input": "5\n10 20 30 40 50\n", "output": "150\n", "is_hidden": True},
                {"input": "2\n-5 5\n", "output": "0\n", "is_hidden": True}
            ]
        },
        {
            "id": "p2222222-2222-2222-2222-222222222222",
            "org_id": FIXTURE_ORGS["org_alpha"]["id"],
            "code": "PALIN",
            "title": "Palindrome Checker",
            "statement": "Given a string S, print 'YES' if it is a palindrome, otherwise 'NO'.",
            "time_limit_ms": 1500,
            "memory_limit_mb": 128,
            "test_cases": [
                {"input": "radar\n", "output": "YES\n", "is_hidden": False},
                {"input": "shodh\n", "output": "NO\n", "is_hidden": True},
                {"input": "racecar\n", "output": "YES\n", "is_hidden": True}
            ]
        },
        {
            "id": "p3333333-3333-3333-3333-333333333333",
            "org_id": FIXTURE_ORGS["org_alpha"]["id"],
            "code": "MAXSUB",
            "title": "Maximum Subarray Sum",
            "statement": "Given an array of integers, compute the maximum contiguous subarray sum.",
            "time_limit_ms": 1500,
            "memory_limit_mb": 128,
            "test_cases": [
                {"input": "-2 1 -3 4 -1 2 1 -5 4\n", "output": "6\n", "is_hidden": False},
                {"input": "1 2 3 4\n", "output": "10\n", "is_hidden": True},
                {"input": "-1 -2 -3\n", "output": "-1\n", "is_hidden": True}
            ]
        }
    ]

    for pdef in problems_def:
        p_existing = (await db.execute(select(Problem).where(Problem.id == pdef["id"]))).scalar_one_or_none()
        if not p_existing:
            prob = Problem(
                id=pdef["id"],
                organization_id=pdef["org_id"],
                code=pdef["code"],
                title=pdef["title"],
                statement=pdef["statement"],
                current_version=1,
                created_at=now - timedelta(days=2)
            )
            db.add(prob)
            await db.flush()

            pv = ProblemVersion(
                id=f"pv-{pdef['id'][3:]}",
                problem_id=prob.id,
                version=1,
                statement=pdef["statement"],
                time_limit_ms=pdef["time_limit_ms"],
                memory_limit_mb=pdef["memory_limit_mb"],
                created_at=now - timedelta(days=2)
            )
            db.add(pv)
            await db.flush()

            for idx, tc in enumerate(pdef["test_cases"], start=1):
                tcase = TestCase(
                    id=f"tc-{idx}-{pdef['id'][5:]}",
                    problem_version_id=pv.id,
                    test_suite_version=1,
                    input_data=tc["input"],
                    expected_output=tc["output"],
                    is_hidden=tc["is_hidden"],
                    order_index=idx,
                    created_at=now - timedelta(days=2)
                )
                db.add(tcase)

    await db.commit()

async def main():
    await init_db()
    async with AsyncSessionLocal() as db:
        await seed_database(db)
        print("Database seeded successfully with deterministic bundle.")

if __name__ == "__main__":
    asyncio.run(main())
