from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.database import get_db
from app.models import Problem, ProblemVersion, TestCase, User
from app.schemas import (
    ProblemCreate, ProblemUpdate, ProblemResponse, TestCaseCreate,
    TestCaseResponse, ProblemVersionResponse
)
from app.auth.dependencies import get_current_user, require_role, verify_organization

router = APIRouter(prefix="/problems", tags=["Problems & Versioning"])

@router.post("", response_model=ProblemResponse, status_code=status.HTTP_201_CREATED)
async def create_problem(
    prob_in: ProblemCreate,
    current_user: User = Depends(require_role(["ORGANIZER"])),
    db: AsyncSession = Depends(get_db)
):
    """
    Creates a new problem with initial ProblemVersion 1 and test cases.
    Organizer role required; scoped to organization.
    """
    # Check duplicate code in org
    existing = (await db.execute(
        select(Problem).where(
            and_(
                Problem.organization_id == current_user.organization_id,
                Problem.code == prob_in.code
            )
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "DUPLICATE_PROBLEM_CODE", "message": f"Problem with code {prob_in.code} already exists."}
        )

    # 1. Create Problem
    problem = Problem(
        organization_id=current_user.organization_id,
        code=prob_in.code,
        title=prob_in.title,
        statement=prob_in.statement,
        current_version=1
    )
    db.add(problem)
    await db.flush()

    # 2. Create ProblemVersion 1
    version1 = ProblemVersion(
        problem_id=problem.id,
        version=1,
        statement=prob_in.statement,
        time_limit_ms=prob_in.time_limit_ms,
        memory_limit_mb=prob_in.memory_limit_mb
    )
    db.add(version1)
    await db.flush()

    # 3. Add test cases
    created_test_cases = []
    for idx, tc in enumerate(prob_in.test_cases, start=1):
        test_case = TestCase(
            problem_version_id=version1.id,
            test_suite_version=1,
            input_data=tc.input_data,
            expected_output=tc.expected_output,
            is_hidden=tc.is_hidden,
            order_index=tc.order_index or idx
        )
        db.add(test_case)
        created_test_cases.append(test_case)

    await db.commit()
    await db.refresh(problem)
    await db.refresh(version1)

    return ProblemResponse(
        id=problem.id,
        organization_id=problem.organization_id,
        code=problem.code,
        title=problem.title,
        statement=problem.statement,
        current_version=problem.current_version,
        created_at=problem.created_at,
        versions=[ProblemVersionResponse.model_validate(version1)],
        test_cases=[TestCaseResponse.model_validate(tc) for tc in created_test_cases]
    )


@router.get("", response_model=List[ProblemResponse])
async def list_problems(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Lists problems scoped to organization with pagination and stable ordering."""
    offset = (page - 1) * limit
    problems = (await db.execute(
        select(Problem)
        .where(Problem.organization_id == current_user.organization_id)
        .order_by(Problem.created_at.asc(), Problem.id.asc())
        .offset(offset)
        .limit(limit)
    )).scalars().all()

    response_items = []
    for p in problems:
        # Load current version
        v = (await db.execute(
            select(ProblemVersion).where(
                and_(
                    ProblemVersion.problem_id == p.id,
                    ProblemVersion.version == p.current_version
                )
            )
        )).scalar_one_or_none()

        # Load test cases
        tcs = []
        if v:
            tc_objs = (await db.execute(
                select(TestCase).where(TestCase.problem_version_id == v.id).order_by(TestCase.order_index.asc())
            )).scalars().all()

            for tc in tc_objs:
                tc_resp = TestCaseResponse(
                    id=tc.id,
                    test_suite_version=tc.test_suite_version,
                    input_data=tc.input_data if (not tc.is_hidden or current_user.role == "ORGANIZER") else None,
                    expected_output=tc.expected_output if (not tc.is_hidden or current_user.role == "ORGANIZER") else None,
                    is_hidden=tc.is_hidden,
                    order_index=tc.order_index
                )
                tcs.append(tc_resp)

        response_items.append(
            ProblemResponse(
                id=p.id,
                organization_id=p.organization_id,
                code=p.code,
                title=p.title,
                statement=p.statement,
                current_version=p.current_version,
                created_at=p.created_at,
                versions=[ProblemVersionResponse.model_validate(v)] if v else [],
                test_cases=tcs
            )
        )
    return response_items


@router.get("/{problem_id}", response_model=ProblemResponse)
async def get_problem(
    problem_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves problem details.
    Guarantees that hidden test inputs and expected outputs are withheld from participants!
    """
    p = (await db.execute(select(Problem).where(Problem.id == problem_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROBLEM_NOT_FOUND", "message": f"Problem {problem_id} not found."}
        )
    verify_organization(current_user, p.organization_id)

    # Load all versions
    versions = (await db.execute(
        select(ProblemVersion).where(ProblemVersion.problem_id == p.id).order_by(ProblemVersion.version.asc())
    )).scalars().all()

    # Load current version test cases
    curr_v = next((v for v in versions if v.version == p.current_version), None)
    tcs = []
    if curr_v:
        tc_objs = (await db.execute(
            select(TestCase).where(TestCase.problem_version_id == curr_v.id).order_by(TestCase.order_index.asc())
        )).scalars().all()

        for tc in tc_objs:
            # Mask hidden test case data for participants
            tc_resp = TestCaseResponse(
                id=tc.id,
                test_suite_version=tc.test_suite_version,
                input_data=tc.input_data if (not tc.is_hidden or current_user.role == "ORGANIZER") else None,
                expected_output=tc.expected_output if (not tc.is_hidden or current_user.role == "ORGANIZER") else None,
                is_hidden=tc.is_hidden,
                order_index=tc.order_index
            )
            tcs.append(tc_resp)

    return ProblemResponse(
        id=p.id,
        organization_id=p.organization_id,
        code=p.code,
        title=p.title,
        statement=p.statement,
        current_version=p.current_version,
        created_at=p.created_at,
        versions=[ProblemVersionResponse.model_validate(v) for v in versions],
        test_cases=tcs
    )


@router.post("/{problem_id}/versions", response_model=ProblemResponse)
async def create_problem_version(
    problem_id: str,
    prob_update: ProblemUpdate,
    current_user: User = Depends(require_role(["ORGANIZER"])),
    db: AsyncSession = Depends(get_db)
):
    """
    Creates a new version of the problem without mutating historical versions.
    Preserves historical problem versions and test cases for past submissions.
    """
    p = (await db.execute(select(Problem).where(Problem.id == problem_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROBLEM_NOT_FOUND", "message": f"Problem {problem_id} not found."}
        )
    verify_organization(current_user, p.organization_id)

    # Get latest version
    curr_v = (await db.execute(
        select(ProblemVersion).where(
            and_(
                ProblemVersion.problem_id == p.id,
                ProblemVersion.version == p.current_version
            )
        )
    )).scalar_one_or_none()

    new_version_num = p.current_version + 1
    new_version = ProblemVersion(
        problem_id=p.id,
        version=new_version_num,
        statement=prob_update.statement or (curr_v.statement if curr_v else p.statement),
        time_limit_ms=prob_update.time_limit_ms or (curr_v.time_limit_ms if curr_v else 2000),
        memory_limit_mb=prob_update.memory_limit_mb or (curr_v.memory_limit_mb if curr_v else 128)
    )
    db.add(new_version)
    await db.flush()

    # Update problem current_version
    p.current_version = new_version_num
    if prob_update.statement:
        p.statement = prob_update.statement

    # Add test cases
    created_tcs = []
    if prob_update.test_cases is not None:
        for idx, tc in enumerate(prob_update.test_cases, start=1):
            new_tc = TestCase(
                problem_version_id=new_version.id,
                test_suite_version=1,
                input_data=tc.input_data,
                expected_output=tc.expected_output,
                is_hidden=tc.is_hidden,
                order_index=tc.order_index or idx
            )
            db.add(new_tc)
            created_tcs.append(new_tc)
    elif curr_v:
        # Copy forward test cases if none specified
        old_tcs = (await db.execute(
            select(TestCase).where(TestCase.problem_version_id == curr_v.id)
        )).scalars().all()
        for tc in old_tcs:
            new_tc = TestCase(
                problem_version_id=new_version.id,
                test_suite_version=tc.test_suite_version,
                input_data=tc.input_data,
                expected_output=tc.expected_output,
                is_hidden=tc.is_hidden,
                order_index=tc.order_index
            )
            db.add(new_tc)
            created_tcs.append(new_tc)

    await db.commit()
    await db.refresh(p)
    return await get_problem(p.id, current_user, db)
