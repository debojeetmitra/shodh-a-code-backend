from datetime import datetime
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, Field, EmailStr

# --- Error Schemas ---
class ErrorResponse(BaseModel):
    code: str = Field(..., description="Machine-readable error code")
    message: str = Field(..., description="Human-readable explanation of error")
    details: Optional[Any] = Field(None, description="Additional context or validation errors")
    request_id: Optional[str] = Field(None, description="Unique trace identifier")

# --- Auth & User Schemas ---
class UserBase(BaseModel):
    username: str
    email: EmailStr
    role: str = Field(..., description="ORGANIZER or PARTICIPANT")

class UserResponse(UserBase):
    id: str
    organization_id: str
    api_key: str
    created_at: datetime

    class Config:
        from_attributes = True

class OrganizationResponse(BaseModel):
    id: str
    name: str
    slug: str
    created_at: datetime

    class Config:
        from_attributes = True

# --- Contest Schemas ---
class ContestCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    description: Optional[str] = None
    start_time: datetime
    end_time: datetime
    penalty_minutes_per_wa: int = Field(default=20, ge=0)

class ContestResponse(BaseModel):
    id: str
    organization_id: str
    title: str
    description: Optional[str]
    start_time: datetime
    end_time: datetime
    status: str
    penalty_minutes_per_wa: int
    created_at: datetime

    class Config:
        from_attributes = True

class ContestRegistrationResponse(BaseModel):
    id: str
    contest_id: str
    user_id: str
    registered_at: datetime
    is_eligible: bool

    class Config:
        from_attributes = True

# --- Problem Schemas ---
class TestCaseCreate(BaseModel):
    input_data: str
    expected_output: str
    is_hidden: bool = False
    order_index: int = 0

class TestCaseResponse(BaseModel):
    id: str
    test_suite_version: int
    input_data: Optional[str] = None
    expected_output: Optional[str] = None
    is_hidden: bool
    order_index: int

    class Config:
        from_attributes = True

class ProblemVersionResponse(BaseModel):
    id: str
    version: int
    statement: str
    time_limit_ms: int
    memory_limit_mb: int
    created_at: datetime

    class Config:
        from_attributes = True

class ProblemCreate(BaseModel):
    code: str = Field(..., min_length=2, max_length=20)
    title: str = Field(..., min_length=3, max_length=200)
    statement: str
    time_limit_ms: int = Field(default=2000, ge=100, le=10000)
    memory_limit_mb: int = Field(default=128, ge=16, le=512)
    test_cases: List[TestCaseCreate] = []

class ProblemUpdate(BaseModel):
    statement: Optional[str] = None
    time_limit_ms: Optional[int] = Field(None, ge=100, le=10000)
    memory_limit_mb: Optional[int] = Field(None, ge=16, le=512)
    test_cases: Optional[List[TestCaseCreate]] = None

class ProblemResponse(BaseModel):
    id: str
    organization_id: str
    code: str
    title: str
    statement: str
    current_version: int
    created_at: datetime
    versions: List[ProblemVersionResponse] = []
    test_cases: List[TestCaseResponse] = []

    class Config:
        from_attributes = True

# --- Submission & Judging Schemas ---
class SubmissionCreate(BaseModel):
    problem_id: str
    code: str = Field(..., min_length=1)
    language: str = Field(default="python", description="Programming language (e.g. python)")

class TestCaseResultResponse(BaseModel):
    test_case_index: int
    is_hidden: bool
    verdict: str
    execution_time_ms: int
    memory_used_kb: int
    stdout: Optional[str] = None
    stderr: Optional[str] = None

    class Config:
        from_attributes = True

class JudgingAttemptResponse(BaseModel):
    id: str
    attempt_number: int
    problem_version_id: str
    test_suite_version: int
    worker_id: Optional[str] = None
    retry_count: int = 0
    status: str
    verdict: str
    execution_time_ms: int
    memory_used_kb: int
    error_details: Optional[str] = None
    execution_mode: str
    is_authoritative: bool
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    test_case_results: List[TestCaseResultResponse] = []

    class Config:
        from_attributes = True

class SubmissionResponse(BaseModel):
    id: str
    contest_id: str
    problem_id: str
    user_id: str
    idempotency_key: Optional[str] = None
    language: str
    code: Optional[str] = None  # Protected: only author or organizer
    problem_version_id: str
    test_suite_version: int
    status: str
    verdict: str
    execution_time_ms: int
    memory_used_kb: int
    active_attempt_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    attempts: List[JudgingAttemptResponse] = []

    class Config:
        from_attributes = True

class RejudgeRequest(BaseModel):
    problem_version: Optional[int] = None
    test_suite_version: Optional[int] = None

# --- Leaderboard Schemas ---
class ParticipantProblemScore(BaseModel):
    problem_id: str
    problem_code: str
    is_solved: bool
    attempts_before_ac: int
    solved_at_minute: Optional[int] = None
    penalty_minutes: int

class LeaderboardRow(BaseModel):
    rank: int
    user_id: str
    username: str
    solved_problems_count: int
    total_penalty_minutes: int
    last_accepted_at: Optional[datetime] = None
    problem_scores: Dict[str, ParticipantProblemScore] = {}

class LeaderboardResponse(BaseModel):
    contest_id: str
    contest_title: str
    status: str
    total_participants: int
    standings: List[LeaderboardRow]
    is_authoritative: bool = True
    generated_at: datetime
