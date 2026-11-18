from typing import List, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.database import get_db
from app.models import Contest, ContestParticipant, User, utcnow, ensure_utc
from app.schemas import ContestCreate, ContestResponse, ContestRegistrationResponse
from app.auth.dependencies import get_current_user, require_role, verify_organization

router = APIRouter(prefix="/contests", tags=["Contests"])

@router.post("", response_model=ContestResponse, status_code=status.HTTP_201_CREATED)
async def create_contest(
    contest_in: ContestCreate,
    current_user: User = Depends(require_role(["ORGANIZER"])),
    db: AsyncSession = Depends(get_db)
):
    """
    Creates a new contest.
    Requires ORGANIZER role. Scoped to the organizer's organization.
    """
    start_utc = ensure_utc(contest_in.start_time)
    end_utc = ensure_utc(contest_in.end_time)
    if end_utc <= start_utc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": "end_time must be strictly after start_time"}
        )

    now = utcnow()
    initial_status = "OPEN" if start_utc <= now <= end_utc else (
        "CLOSED" if now > end_utc else "DRAFT"
    )

    contest = Contest(
        organization_id=current_user.organization_id,
        title=contest_in.title,
        description=contest_in.description,
        start_time=contest_in.start_time,
        end_time=contest_in.end_time,
        status=initial_status,
        penalty_minutes_per_wa=contest_in.penalty_minutes_per_wa
    )
    db.add(contest)
    await db.commit()
    await db.refresh(contest)
    return contest


@router.get("", response_model=List[ContestResponse])
async def list_contests(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Page size limit"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Lists contests with pagination and stable ordering.
    Scoped to the authenticated user's organization.
    """
    offset = (page - 1) * limit
    stmt = (
        select(Contest)
        .where(Contest.organization_id == current_user.organization_id)
        .order_by(Contest.created_at.desc(), Contest.id.asc())
        .offset(offset)
        .limit(limit)
    )
    res = await db.execute(stmt)
    return res.scalars().all()


@router.get("/{contest_id}", response_model=ContestResponse)
async def get_contest(
    contest_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retrieves contest details and verifies organization boundary."""
    res = await db.execute(select(Contest).where(Contest.id == contest_id))
    contest = res.scalar_one_or_none()
    if not contest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CONTEST_NOT_FOUND", "message": f"Contest {contest_id} not found."}
        )
    verify_organization(current_user, contest.organization_id)
    return contest


@router.post("/{contest_id}/join", response_model=ContestRegistrationResponse)
async def join_contest(
    contest_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Registers the authenticated user for a contest.
    Enforces organization membership and contest state.
    """
    res = await db.execute(select(Contest).where(Contest.id == contest_id))
    contest = res.scalar_one_or_none()
    if not contest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CONTEST_NOT_FOUND", "message": f"Contest {contest_id} not found."}
        )
    verify_organization(current_user, contest.organization_id)

    now = utcnow()
    end_time_utc = ensure_utc(contest.end_time)
    if now > end_time_utc or contest.status == "CLOSED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "CONTEST_CLOSED", "message": "Cannot join a closed contest."}
        )

    # Check existing registration
    reg_res = await db.execute(
        select(ContestParticipant).where(
            and_(
                ContestParticipant.contest_id == contest_id,
                ContestParticipant.user_id == current_user.id
            )
        )
    )
    existing_reg = reg_res.scalar_one_or_none()
    if existing_reg:
        return existing_reg

    reg = ContestParticipant(
        contest_id=contest_id,
        user_id=current_user.id,
        is_eligible=True
    )
    db.add(reg)
    await db.commit()
    await db.refresh(reg)
    return reg
