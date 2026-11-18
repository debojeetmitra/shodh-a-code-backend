import asyncio
from typing import Optional
from app.judge.runner_interface import TestCaseExecutionResult, Verdict, ExecutionMode

class TestDoubleRunner:
    """
    Deterministic test double runner for controlled races, delays, and fault injection:
    - Injected infrastructure crashes / system errors
    - Artificial delay for leasing and timeout testing
    - Forced verdicts for deterministic scenario execution
    - Clearly labeled with execution_mode = TEST_DOUBLE
    """

    def __init__(
        self,
        inject_system_error: bool = False,
        inject_delay_sec: float = 0.0,
        forced_verdict: Optional[Verdict] = None
    ):
        self.inject_system_error = inject_system_error
        self.inject_delay_sec = inject_delay_sec
        self.forced_verdict = forced_verdict
        self.execution_mode = ExecutionMode.TEST_DOUBLE

    async def execute(
        self,
        code: str,
        input_data: str,
        expected_output: str,
        time_limit_ms: int = 2000,
        memory_limit_mb: int = 128
    ) -> TestCaseExecutionResult:
        if self.inject_delay_sec > 0:
            await asyncio.sleep(self.inject_delay_sec)

        if self.inject_system_error or "CRASH_INFRASTRUCTURE" in code:
            return TestCaseExecutionResult(
                verdict=Verdict.SYSTEM_ERROR,
                execution_time_ms=5,
                memory_used_kb=1024,
                stdout="",
                stderr="Simulated judge infrastructure crash: runner host lost connection",
                error_message="Judge infrastructure crash injected for resilience testing",
                execution_mode=self.execution_mode
            )

        if self.forced_verdict:
            return TestCaseExecutionResult(
                verdict=self.forced_verdict,
                execution_time_ms=10,
                memory_used_kb=2048,
                stdout="Mock Output",
                stderr="",
                execution_mode=self.execution_mode
            )

        # Basic deterministic mock execution:
        clean_input = input_data.strip()
        clean_expected = expected_output.strip()

        # Check code keywords for intentional edge cases in tests
        if "SIMULATE_TIMEOUT" in code or "while True:" in code:
            return TestCaseExecutionResult(
                verdict=Verdict.TIME_LIMIT_EXCEEDED,
                execution_time_ms=time_limit_ms,
                memory_used_kb=2048,
                stdout="",
                stderr="Time limit exceeded",
                execution_mode=self.execution_mode
            )
        if "SIMULATE_RUNTIME_ERROR" in code or "1 / 0" in code:
            return TestCaseExecutionResult(
                verdict=Verdict.RUNTIME_ERROR,
                execution_time_ms=10,
                memory_used_kb=2048,
                stdout="",
                stderr="ZeroDivisionError: division by zero",
                execution_mode=self.execution_mode
            )
        if "SIMULATE_WRONG_ANSWER" in code:
            return TestCaseExecutionResult(
                verdict=Verdict.WRONG_ANSWER,
                execution_time_ms=12,
                memory_used_kb=2048,
                stdout="Incorrect Answer",
                stderr="",
                execution_mode=self.execution_mode
            )

        # By default simulate correct execution matching expected output
        return TestCaseExecutionResult(
            verdict=Verdict.ACCEPTED,
            execution_time_ms=15,
            memory_used_kb=2048,
            stdout=clean_expected,
            stderr="",
            execution_mode=self.execution_mode
        )
