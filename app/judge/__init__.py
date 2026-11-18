from typing import Optional
from app.config import settings
from app.judge.runner_interface import Verdict, ExecutionMode, TestCaseExecutionResult
from app.judge.sandbox_runner import SandboxRunner
from app.judge.docker_runner import DockerRunner
from app.judge.test_double_runner import TestDoubleRunner

def get_runner(mode: Optional[str] = None):
    chosen_mode = mode or settings.EXECUTION_MODE
    if settings.INJECT_RUNNER_FAILURE:
        return TestDoubleRunner(inject_system_error=True)

    if chosen_mode == ExecutionMode.TEST_DOUBLE:
        return TestDoubleRunner()
    elif chosen_mode == ExecutionMode.REAL_DOCKER:
        return DockerRunner()
    else:
        return SandboxRunner()
