from datetime import datetime, timezone
import json
from typing import List, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from app.models import Contest, Submission, JudgingAttempt, StandingsRecord, User, Problem, ensure_utc

async def compute_contest_standings(
    contest_id: str,
    db: AsyncSession
) -> List[Dict[str, Any]]:
    """
    Computes deterministic contest standings directly from authoritative persistent records:
    - Only considers authoritative completed judging attempts.
    - System errors are excluded from penalty calculations.
    - Uses deterministic tie-breaking:
      1. Solved problems count (DESC)
      2. Total penalty minutes (ASC)
      3. Last accepted submission timestamp (ASC)
      4. Username (ASC)
    """
    # 1. Fetch contest
    contest_res = await db.execute(select(Contest).where(Contest.id == contest_id))
    contest = contest_res.scalar_one_or_none()
    if not contest:
        return []

    # 2. Fetch all submissions for this contest with their authoritative attempt
    # Submissions are ordered chronologically by created_at
    query = (
        select(Submission, JudgingAttempt, Problem.code, User.username)
        .join(User, Submission.user_id == User.id)
        .join(Problem, Submission.problem_id == Problem.id)
        .outerjoin(
            JudgingAttempt,
            and_(
                JudgingAttempt.submission_id == Submission.id,
                JudgingAttempt.is_authoritative == True,
                JudgingAttempt.status == "COMPLETED"
            )
        )
        .where(Submission.contest_id == contest_id)
        .order_by(Submission.created_at.asc())
    )

    rows = (await db.execute(query)).all()

    # user_id -> { "username": ..., "problems": { problem_id: { "solved": bool, "attempts_before": int, "solved_at": ..., "penalty": int, "code": ... } } }
    participants_map: Dict[str, Dict[str, Any]] = {}

    for sub, attempt, prob_code, username in rows:
        uid = sub.user_id
        if uid not in participants_map:
            participants_map[uid] = {
                "user_id": uid,
                "username": username,
                "problems": {}
            }

        prob_id = sub.problem_id
        if prob_id not in participants_map[uid]["problems"]:
            participants_map[uid]["problems"][prob_id] = {
                "problem_id": prob_id,
                "problem_code": prob_code,
                "is_solved": False,
                "attempts_before_ac": 0,
                "solved_at": None,
                "solved_at_minute": None,
                "penalty_minutes": 0
            }

        pdata = participants_map[uid]["problems"][prob_id]

        # If already solved by an earlier submission, ignore subsequent submissions
        if pdata["is_solved"]:
            continue

        # If no completed authoritative attempt, verdict is pending
        if not attempt:
            continue

        verdict = attempt.verdict

        if verdict == "ACCEPTED":
            pdata["is_solved"] = True
            pdata["solved_at"] = sub.created_at
            
            # Minute elapsed since contest start
            elapsed_sec = max(0, (ensure_utc(sub.created_at) - ensure_utc(contest.start_time)).total_seconds())
            minute = int(elapsed_sec // 60)
            pdata["solved_at_minute"] = minute
            pdata["penalty_minutes"] = minute + (pdata["attempts_before_ac"] * contest.penalty_minutes_per_wa)
        elif verdict in ["WRONG_ANSWER", "RUNTIME_ERROR", "COMPILATION_ERROR", "TIME_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED", "MEMORY_LIMIT_EXCEEDED"]:
            # Valid student mistake: increment penalty count
            pdata["attempts_before_ac"] += 1
        elif verdict == "SYSTEM_ERROR":
            # SPEC REQUIREMENT: "A runner crash or unavailable service must not become a student error or reduce the student's score."
            # Do NOT penalize the participant for system/infrastructure failure!
            pass

    # Aggregate totals
    standings_list = []
    for uid, p in participants_map.items():
        solved_count = sum(1 for pdata in p["problems"].values() if pdata["is_solved"])
        total_penalty = sum(pdata["penalty_minutes"] for pdata in p["problems"].values() if pdata["is_solved"])
        
        solved_times = [pdata["solved_at"] for pdata in p["problems"].values() if pdata["is_solved"] and pdata["solved_at"]]
        last_accepted_at = max(solved_times) if solved_times else None

        standings_list.append({
            "user_id": uid,
            "username": p["username"],
            "solved_problems_count": solved_count,
            "total_penalty_minutes": total_penalty,
            "last_accepted_at": last_accepted_at,
            "problem_scores": p["problems"]
        })

    # Sort deterministically:
    # 1. solved_problems_count DESC
    # 2. total_penalty_minutes ASC
    # 3. last_accepted_at ASC (None treated as latest)
    # 4. username ASC
    def sort_key(row):
        last_time_ts = row["last_accepted_at"].timestamp() if row["last_accepted_at"] else 9999999999.0
        return (
            -row["solved_problems_count"],
            row["total_penalty_minutes"],
            last_time_ts,
            row["username"]
        )

    standings_list.sort(key=sort_key)

    # Assign ranks
    for idx, row in enumerate(standings_list, start=1):
        row["rank"] = idx

    return standings_list


async def refresh_standings_cache(contest_id: str, db: AsyncSession) -> List[Dict[str, Any]]:
    """
    Recomputes standings and updates the persistent standings_records table atomically.
    Ensures that rebuilding standings from authoritative records produces the exact same result as the serving path.
    """
    standings = await compute_contest_standings(contest_id, db)

    # Upsert or replace standings_records for this contest
    # Delete old cached records for this contest
    existing_records = (await db.execute(
        select(StandingsRecord).where(StandingsRecord.contest_id == contest_id)
    )).scalars().all()

    record_by_user = {rec.user_id: rec for rec in existing_records}

    for item in standings:
        uid = item["user_id"]
        # Serialize problem scores to JSON
        details = {
            prob_id: {
                "problem_code": p["problem_code"],
                "is_solved": p["is_solved"],
                "attempts_before_ac": p["attempts_before_ac"],
                "solved_at_minute": p["solved_at_minute"],
                "penalty_minutes": p["penalty_minutes"]
            }
            for prob_id, p in item["problem_scores"].items()
        }

        if uid in record_by_user:
            rec = record_by_user[uid]
            rec.solved_problems_count = item["solved_problems_count"]
            rec.total_penalty_minutes = item["total_penalty_minutes"]
            rec.last_accepted_at = item["last_accepted_at"]
            rec.rank = item["rank"]
            rec.details_json = json.dumps(details)
        else:
            new_rec = StandingsRecord(
                contest_id=contest_id,
                user_id=uid,
                solved_problems_count=item["solved_problems_count"],
                total_penalty_minutes=item["total_penalty_minutes"],
                last_accepted_at=item["last_accepted_at"],
                rank=item["rank"],
                details_json=json.dumps(details)
            )
            db.add(new_rec)

    await db.commit()
    return standings
