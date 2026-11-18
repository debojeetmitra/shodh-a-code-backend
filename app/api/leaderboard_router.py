import json
from datetime import datetime, timezone
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Contest, StandingsRecord, User, utcnow
from app.schemas import LeaderboardResponse, LeaderboardRow, ParticipantProblemScore
from app.auth.dependencies import get_current_user, require_role, verify_organization
from app.judge.scorer import compute_contest_standings, refresh_standings_cache

router = APIRouter(prefix="/contests", tags=["Leaderboard & Standings"])

@router.get("/{contest_id}/leaderboard", response_model=LeaderboardResponse)
async def get_contest_leaderboard(
    contest_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves the current contest leaderboard.
    Reads from the serving standings table with fallback to on-the-fly calculation.
    """
    contest = (await db.execute(select(Contest).where(Contest.id == contest_id))).scalar_one_or_none()
    if not contest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CONTEST_NOT_FOUND", "message": f"Contest {contest_id} not found."}
        )
    verify_organization(current_user, contest.organization_id)

    # Fetch standings records
    records = (await db.execute(
        select(StandingsRecord, User.username)
        .join(User, StandingsRecord.user_id == User.id)
        .where(StandingsRecord.contest_id == contest_id)
        .order_by(StandingsRecord.rank.asc(), StandingsRecord.solved_problems_count.desc(), StandingsRecord.total_penalty_minutes.asc())
    )).all()

    if not records:
        # Compute on the fly if not yet cached
        computed = await refresh_standings_cache(contest_id, db)
        return LeaderboardResponse(
            contest_id=contest.id,
            contest_title=contest.title,
            status=contest.status,
            total_participants=len(computed),
            standings=[
                LeaderboardRow(
                    rank=r["rank"],
                    user_id=r["user_id"],
                    username=r["username"],
                    solved_problems_count=r["solved_problems_count"],
                    total_penalty_minutes=r["total_penalty_minutes"],
                    last_accepted_at=r["last_accepted_at"],
                    problem_scores={
                        pid: ParticipantProblemScore(
                            problem_id=pid,
                            problem_code=pdata["problem_code"],
                            is_solved=pdata["is_solved"],
                            attempts_before_ac=pdata["attempts_before_ac"],
                            solved_at_minute=pdata["solved_at_minute"],
                            penalty_minutes=pdata["penalty_minutes"]
                        )
                        for pid, pdata in r["problem_scores"].items()
                    }
                )
                for r in computed
            ],
            is_authoritative=True,
            generated_at=utcnow()
        )

    rows = []
    for rec, uname in records:
        details = json.loads(rec.details_json or "{}")
        scores = {
            pid: ParticipantProblemScore(
                problem_id=pid,
                problem_code=val.get("problem_code", ""),
                is_solved=val.get("is_solved", False),
                attempts_before_ac=val.get("attempts_before_ac", 0),
                solved_at_minute=val.get("solved_at_minute"),
                penalty_minutes=val.get("penalty_minutes", 0)
            )
            for pid, val in details.items()
        }
        rows.append(
            LeaderboardRow(
                rank=rec.rank,
                user_id=rec.user_id,
                username=uname,
                solved_problems_count=rec.solved_problems_count,
                total_penalty_minutes=rec.total_penalty_minutes,
                last_accepted_at=rec.last_accepted_at,
                problem_scores=scores
            )
        )

    return LeaderboardResponse(
        contest_id=contest.id,
        contest_title=contest.title,
        status=contest.status,
        total_participants=len(rows),
        standings=rows,
        is_authoritative=True,
        generated_at=utcnow()
    )


@router.post("/{contest_id}/leaderboard/rebuild", response_model=LeaderboardResponse)
async def rebuild_contest_leaderboard(
    contest_id: str,
    current_user: User = Depends(require_role(["ORGANIZER"])),
    db: AsyncSession = Depends(get_db)
):
    """
    Rebuilds contest standings directly from authoritative persistent records.
    Organizer only. Guarantees cache consistency and reconciles any out-of-order completions.
    """
    contest = (await db.execute(select(Contest).where(Contest.id == contest_id))).scalar_one_or_none()
    if not contest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CONTEST_NOT_FOUND", "message": f"Contest {contest_id} not found."}
        )
    verify_organization(current_user, contest.organization_id)

    # Rebuild directly from authoritative records
    computed = await refresh_standings_cache(contest_id, db)

    return LeaderboardResponse(
        contest_id=contest.id,
        contest_title=contest.title,
        status=contest.status,
        total_participants=len(computed),
        standings=[
            LeaderboardRow(
                rank=r["rank"],
                user_id=r["user_id"],
                username=r["username"],
                solved_problems_count=r["solved_problems_count"],
                total_penalty_minutes=r["total_penalty_minutes"],
                last_accepted_at=r["last_accepted_at"],
                problem_scores={
                    pid: ParticipantProblemScore(
                        problem_id=pid,
                        problem_code=pdata["problem_code"],
                        is_solved=pdata["is_solved"],
                        attempts_before_ac=pdata["attempts_before_ac"],
                        solved_at_minute=pdata["solved_at_minute"],
                        penalty_minutes=pdata["penalty_minutes"]
                    )
                    for pid, pdata in r["problem_scores"].items()
                }
            )
            for r in computed
        ],
        is_authoritative=True,
        generated_at=utcnow()
    )
