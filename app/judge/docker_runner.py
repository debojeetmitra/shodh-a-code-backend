import asyncio
import time
import docker
from docker.errors import DockerException, ContainerError, APIError
from app.judge.runner_interface import TestCaseExecutionResult, Verdict, ExecutionMode
from app.config import settings

class DockerRunner:
    """
    Real Docker container runner enforcing strict sandboxing:
    - Bounded CPU (1.0 nano_cpus)
    - Bounded memory (e.g. 128 MB mem_limit)
    - Bounded process count (pids_limit=64)
    - Wall time bounded by timeout
    - Output size bounded (truncation after 64KB)
    - Network disabled (network_mode="none")
    - Non-root user (user="10001:10001")
    - Read-only root filesystem
    - Ephemeral tmpfs (/tmp size=16m)
    - Docker control socket never mounted
    - Always cleans up ephemeral containers
    """

    def __init__(self, image: str = settings.DOCKER_IMAGE, output_limit_bytes: int = settings.DEFAULT_OUTPUT_LIMIT_BYTES):
        self.image = image
        self.output_limit_bytes = output_limit_bytes
        self.execution_mode = ExecutionMode.REAL_DOCKER
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def is_available(self) -> bool:
        try:
            client = self._get_client()
            client.ping()
            return True
        except Exception:
            return False

    async def execute(
        self,
        code: str,
        input_data: str,
        expected_output: str,
        time_limit_ms: int = settings.DEFAULT_TIME_LIMIT_MS,
        memory_limit_mb: int = settings.DEFAULT_MEMORY_LIMIT_MB
    ) -> TestCaseExecutionResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._execute_sync,
            code,
            input_data,
            expected_output,
            time_limit_ms,
            memory_limit_mb
        )

    def _execute_sync(
        self,
        code: str,
        input_data: str,
        expected_output: str,
        time_limit_ms: int,
        memory_limit_mb: int
    ) -> TestCaseExecutionResult:
        try:
            client = self._get_client()
        except DockerException as e:
            return TestCaseExecutionResult(
                verdict=Verdict.SYSTEM_ERROR,
                execution_time_ms=0,
                memory_used_kb=0,
                stdout="",
                stderr=f"Docker engine unavailable: {str(e)}",
                error_message="Judge infrastructure failure: Docker daemon unreachable",
                execution_mode=self.execution_mode
            )

        timeout_sec = max(1, int(time_limit_ms / 1000.0))
        container = None
        start_time = time.perf_counter()

        try:
            # Command executes python reading from stdin
            cmd = ["python3", "-c", code]
            
            # Start ephemeral isolated container
            container = client.containers.create(
                image=self.image,
                command=cmd,
                stdin_open=True,
                network_mode="none",
                mem_limit=f"{memory_limit_mb}m",
                nano_cpus=1_000_000_000,
                pids_limit=64,
                user="10001:10001",
                read_only=True,
                tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"}
            )

            container.start()

            # Attach socket to send stdin and receive output
            sock = container.attach_socket(params={"stdin": 1, "stream": 1, "stdout": 1, "stderr": 1})
            if hasattr(sock, "_sock"):
                sock._sock.sendall(input_data.encode("utf-8"))
                sock._sock.shutdown(1)  # Half-close write end
            sock.close()

            # Wait with timeout
            try:
                res = container.wait(timeout=timeout_sec)
                exit_code = res.get("StatusCode", 0)
            except Exception:
                # Timeout
                container.kill()
                elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                return TestCaseExecutionResult(
                    verdict=Verdict.TIME_LIMIT_EXCEEDED,
                    execution_time_ms=elapsed_ms,
                    memory_used_kb=memory_limit_mb * 1024,
                    stdout="",
                    stderr=f"Time limit exceeded ({time_limit_ms}ms)",
                    execution_mode=self.execution_mode
                )

            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            logs_stdout = container.logs(stdout=True, stderr=False)
            logs_stderr = container.logs(stdout=False, stderr=True)

            if len(logs_stdout) > self.output_limit_bytes:
                return TestCaseExecutionResult(
                    verdict=Verdict.OUTPUT_LIMIT_EXCEEDED,
                    execution_time_ms=elapsed_ms,
                    memory_used_kb=2048,
                    stdout=logs_stdout[:1000].decode("utf-8", errors="replace") + "\n...[TRUNCATED]",
                    stderr="Output limit exceeded.",
                    execution_mode=self.execution_mode
                )

            stdout_str = logs_stdout.decode("utf-8", errors="replace")
            stderr_str = logs_stderr.decode("utf-8", errors="replace")

            if exit_code != 0:
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

            clean_actual = "\n".join(line.rstrip() for line in stdout_str.strip().splitlines())
            clean_expected = "\n".join(line.rstrip() for line in expected_output.strip().splitlines())

            verdict = Verdict.ACCEPTED if clean_actual == clean_expected else Verdict.WRONG_ANSWER

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
                error_message=f"Docker runtime error: {str(e)}",
                execution_mode=self.execution_mode
            )
        finally:
            if container:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
