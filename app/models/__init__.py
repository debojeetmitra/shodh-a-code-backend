import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, ForeignKey, Text,
    UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from app.database import Base

def generate_uuid() -> str:
    return str(uuid.uuid4())

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def ensure_utc(dt: datetime) -> datetime:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

class Organization(Base):
    __tablename__ = "organizations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(100), nullable=False)
    slug = Column(String(50), unique=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    users = relationship("User", back_populates="organization", cascade="all, delete-orphan")
    contests = relationship("Contest", back_populates="organization", cascade="all, delete-orphan")
    problems = relationship("Problem", back_populates="organization", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    username = Column(String(50), nullable=False)
    email = Column(String(100), nullable=False)
    role = Column(String(20), nullable=False)  # ORGANIZER, PARTICIPANT
    api_key = Column(String(64), unique=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "username", name="uq_user_org_username"),
    )

    organization = relationship("Organization", back_populates="users")
    submissions = relationship("Submission", back_populates="user")
    participations = relationship("ContestParticipant", back_populates="user")


class Contest(Base):
    __tablename__ = "contests"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(20), default="OPEN", nullable=False)  # DRAFT, OPEN, CLOSED
    penalty_minutes_per_wa = Column(Integer, default=20, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    organization = relationship("Organization", back_populates="contests")
    participants = relationship("ContestParticipant", back_populates="contest", cascade="all, delete-orphan")
    submissions = relationship("Submission", back_populates="contest", cascade="all, delete-orphan")
    standings = relationship("StandingsRecord", back_populates="contest", cascade="all, delete-orphan")


class ContestParticipant(Base):
    __tablename__ = "contest_participants"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    contest_id = Column(String(36), ForeignKey("contests.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    registered_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    is_eligible = Column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint("contest_id", "user_id", name="uq_contest_user_participant"),
    )

    contest = relationship("Contest", back_populates="participants")
    user = relationship("User", back_populates="participations")


class Problem(Base):
    __tablename__ = "problems"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    code = Column(String(20), nullable=False)
    title = Column(String(200), nullable=False)
    statement = Column(Text, nullable=False)
    current_version = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_problem_org_code"),
    )

    organization = relationship("Organization", back_populates="problems")
    versions = relationship("ProblemVersion", back_populates="problem", cascade="all, delete-orphan")
    submissions = relationship("Submission", back_populates="problem")


class ProblemVersion(Base):
    __tablename__ = "problem_versions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    problem_id = Column(String(36), ForeignKey("problems.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    statement = Column(Text, nullable=False)
    time_limit_ms = Column(Integer, default=2000, nullable=False)
    memory_limit_mb = Column(Integer, default=128, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("problem_id", "version", name="uq_problem_version"),
    )

    problem = relationship("Problem", back_populates="versions")
    test_cases = relationship("TestCase", back_populates="problem_version", cascade="all, delete-orphan")


class TestCase(Base):
    __tablename__ = "test_cases"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    problem_version_id = Column(String(36), ForeignKey("problem_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    test_suite_version = Column(Integer, default=1, nullable=False)
    input_data = Column(Text, nullable=False)
    expected_output = Column(Text, nullable=False)
    is_hidden = Column(Boolean, default=False, nullable=False)
    order_index = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    problem_version = relationship("ProblemVersion", back_populates="test_cases")


class Submission(Base):
    __tablename__ = "submissions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    contest_id = Column(String(36), ForeignKey("contests.id", ondelete="CASCADE"), nullable=False, index=True)
    problem_id = Column(String(36), ForeignKey("problems.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    idempotency_key = Column(String(100), nullable=True, index=True)
    request_hash = Column(String(64), nullable=False)
    code = Column(Text, nullable=False)
    language = Column(String(20), default="python", nullable=False)
    problem_version_id = Column(String(36), ForeignKey("problem_versions.id"), nullable=False)
    test_suite_version = Column(Integer, default=1, nullable=False)
    status = Column(String(20), default="QUEUED", nullable=False, index=True)  # QUEUED, RUNNING, COMPLETED, FAILED
    verdict = Column(String(30), default="PENDING", nullable=False, index=True)  # ACCEPTED, WRONG_ANSWER, etc.
    execution_time_ms = Column(Integer, default=0, nullable=False)
    memory_used_kb = Column(Integer, default=0, nullable=False)
    active_attempt_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("contest_id", "user_id", "idempotency_key", name="uq_submission_idempotency"),
        Index("idx_submission_contest_user", "contest_id", "user_id"),
        Index("idx_submission_verdict_authoritative", "contest_id", "verdict", "status"),
    )

    contest = relationship("Contest", back_populates="submissions")
    problem = relationship("Problem", back_populates="submissions")
    user = relationship("User", back_populates="submissions")
    attempts = relationship("JudgingAttempt", back_populates="submission", cascade="all, delete-orphan", order_by="JudgingAttempt.attempt_number")


class JudgingAttempt(Base):
    __tablename__ = "judging_attempts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    submission_id = Column(String(36), ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True)
    attempt_number = Column(Integer, default=1, nullable=False)
    problem_version_id = Column(String(36), ForeignKey("problem_versions.id"), nullable=False)
    test_suite_version = Column(Integer, default=1, nullable=False)
    worker_id = Column(String(64), nullable=True)
    status = Column(String(20), default="QUEUED", nullable=False, index=True)  # QUEUED, RUNNING, COMPLETED, FAILED
    verdict = Column(String(30), default="PENDING", nullable=False, index=True)
    execution_time_ms = Column(Integer, default=0, nullable=False)
    memory_used_kb = Column(Integer, default=0, nullable=False)
    error_details = Column(Text, nullable=True)
    failure_type = Column(String(50), nullable=True)
    execution_mode = Column(String(30), default="SANDBOX_SUBPROCESS", nullable=False)  # REAL_DOCKER, SANDBOX_SUBPROCESS, TEST_DOUBLE
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    retry_count = Column(Integer, default=0, nullable=False)
    is_authoritative = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("submission_id", "attempt_number", name="uq_submission_attempt_number"),
        Index("idx_attempt_queue", "status", "lease_expires_at"),
    )

    submission = relationship("Submission", back_populates="attempts")
    test_case_results = relationship("JudgingTestCaseResult", back_populates="attempt", cascade="all, delete-orphan")


class JudgingTestCaseResult(Base):
    __tablename__ = "judging_test_case_results"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    attempt_id = Column(String(36), ForeignKey("judging_attempts.id", ondelete="CASCADE"), nullable=False, index=True)
    test_case_id = Column(String(36), ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False)
    test_case_index = Column(Integer, nullable=False)
    is_hidden = Column(Boolean, default=False, nullable=False)
    verdict = Column(String(30), nullable=False)  # ACCEPTED, WRONG_ANSWER, TIME_LIMIT_EXCEEDED, etc.
    execution_time_ms = Column(Integer, default=0, nullable=False)
    memory_used_kb = Column(Integer, default=0, nullable=False)
    stdout_truncated = Column(Text, nullable=True)
    stderr_truncated = Column(Text, nullable=True)

    attempt = relationship("JudgingAttempt", back_populates="test_case_results")


class StandingsRecord(Base):
    __tablename__ = "standings_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    contest_id = Column(String(36), ForeignKey("contests.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    solved_problems_count = Column(Integer, default=0, nullable=False)
    total_penalty_minutes = Column(Integer, default=0, nullable=False)
    last_accepted_at = Column(DateTime(timezone=True), nullable=True)
    rank = Column(Integer, default=0, nullable=False)
    details_json = Column(Text, default="{}", nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("contest_id", "user_id", name="uq_standings_contest_user"),
        Index("idx_standings_rank", "contest_id", "solved_problems_count", "total_penalty_minutes"),
    )

    contest = relationship("Contest", back_populates="standings")
    user = relationship("User")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    request_id = Column(String(64), nullable=True, index=True)
    submission_id = Column(String(36), nullable=True, index=True)
    attempt_id = Column(String(36), nullable=True, index=True)
    actor_id = Column(String(36), nullable=True, index=True)
    action = Column(String(100), nullable=False)
    details = Column(Text, nullable=True)
    timestamp = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
