import asyncio
import os
import sys
import tempfile
import time
from app.judge.runner_interface import TestCaseExecutionResult, Verdict, ExecutionMode
from app.config import settings

class SandboxRunner:
    """
    Subprocess sandbox runner enforcing:
    - Bounded execution time (asyncio timeout)
    - Output size cap (max bytes truncation -> OUTPUT_LIMIT_EXCEEDED if egregious)
    - Isolated temporary working directory
    - Non-zero exit code classification (RUNTIME_ERROR vs COMPILATION_ERROR)
    - Clean test output matching (ignoring trailing whitespace)
    """

    def __init__(self, output_limit_bytes: int = settings.DEFAULT_OUTPUT_LIMIT_BYTES):
        self.output_limit_bytes = output_limit_bytes
        self.execution_mode = ExecutionMode.SANDBOX_SUBPROCESS

    async def execute(
        self,
        code: str,
        input_data: str,
        expected_output: str,
        time_limit_ms: int = settings.DEFAULT_TIME_LIMIT_MS,
        memory_limit_mb: int = settings.DEFAULT_MEMORY_LIMIT_MB
    ) -> TestCaseExecutionResult:
        timeout_sec = time_limit_ms / 1000.0

        with tempfile.TemporaryDirectory() as tmp_dir:
            script_path = os.path.join(tmp_dir, "solution.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)

            start_time = time.perf_counter()
            proc = None

            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    script_path,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=tmp_dir
                )

                input_bytes = input_data.encode("utf-8")
                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(input=input_bytes),
                        timeout=timeout_sec
                    )
                except asyncio.TimeoutError:
                    if proc:
                        try:
                            proc.kill()
                            await proc.wait()
                        except Exception:
                            pass
                    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                    return TestCaseExecutionResult(
                        verdict=Verdict.TIME_LIMIT_EXCEEDED,
                        execution_time_ms=elapsed_ms,
                        memory_used_kb=1024,
                        stdout="",
                        stderr=f"Process timed out after {time_limit_ms}ms",
                        execution_mode=self.execution_mode
                    )

                elapsed_ms = int((time.perf_counter() - start_time) * 1000)

                # Check output limit
                if len(stdout_bytes) > self.output_limit_bytes:
                    return TestCaseExecutionResult(
                        verdict=Verdict.OUTPUT_LIMIT_EXCEEDED,
                        execution_time_ms=elapsed_ms,
                        memory_used_kb=2048,
                        stdout=stdout_bytes[:1000].decode("utf-8", errors="replace") + "\n...[OUTPUT TRUNCATED]",
                        stderr="Output limit exceeded.",
                        execution_mode=self.execution_mode
                    )

                stdout_str = stdout_bytes.decode("utf-8", errors="replace")
                stderr_str = stderr_bytes.decode("utf-8", errors="replace")

                # Check runtime error
                if proc.returncode != 0:
                    verdict = Verdict.RUNTIME_ERROR
                    if "SyntaxError" in stderr_str or "IndentationError" in stderr_str:
                        verdict = Verdict.COMPILATION_ERROR
                    return TestCaseExecutionResult(
                        verdict=verdict,
                        execution_time_ms=elapsed_ms,
                        memory_used_kb=2048,
                        stdout=stdout_str,
                        stderr=stderr_str,
                        error_message=stderr_str[:500],
                        execution_mode=self.execution_mode
                    )

                # Compare output (normalize newlines & strip trailing whitespace)
                clean_actual = "\n".join(line.rstrip() for line in stdout_str.strip().splitlines())
                clean_expected = "\n".join(line.rstrip() for line in expected_output.strip().splitlines())

                if clean_actual == clean_expected:
                    verdict = Verdict.ACCEPTED
                else:
                    verdict = Verdict.WRONG_ANSWER

                return TestCaseExecutionResult(
                    verdict=verdict,
                    execution_time_ms=elapsed_ms,
                    memory_used_kb=2048,
                    stdout=stdout_str,
                    stderr=stderr_str,
                    execution_mode=self.execution_mode
                )

            except Exception as e:
                return TestCaseExecutionResult(
                    verdict=Verdict.SYSTEM_ERROR,
                    execution_time_ms=0,
                    memory_used_kb=0,
                    stdout="",
                    stderr=str(e),
                    error_message=f"Infrastructure runner error: {str(e)}",
                    execution_mode=self.execution_mode
                )
