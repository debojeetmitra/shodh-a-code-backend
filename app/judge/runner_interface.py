from dataclasses import dataclass
from typing import Optional
from enum import Enum

class Verdict(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    WRONG_ANSWER = "WRONG_ANSWER"
    COMPILATION_ERROR = "COMPILATION_ERROR"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    TIME_LIMIT_EXCEEDED = "TIME_LIMIT_EXCEEDED"
    MEMORY_LIMIT_EXCEEDED = "MEMORY_LIMIT_EXCEEDED"
    OUTPUT_LIMIT_EXCEEDED = "OUTPUT_LIMIT_EXCEEDED"
    SYSTEM_ERROR = "SYSTEM_ERROR"

class ExecutionMode(str, Enum):
    REAL_DOCKER = "REAL_DOCKER"
    SANDBOX_SUBPROCESS = "SANDBOX_SUBPROCESS"
    TEST_DOUBLE = "TEST_DOUBLE"

@dataclass
class TestCaseExecutionResult:
    verdict: Verdict
    execution_time_ms: int
    memory_used_kb: int
    stdout: str
    stderr: str
    error_message: Optional[str] = None
    execution_mode: str = ExecutionMode.SANDBOX_SUBPROCESS
