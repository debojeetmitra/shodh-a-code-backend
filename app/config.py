import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Shodh a Code"
    VERSION: str = "1.0.0"
    API_V1_PREFIX: str = "/api/v1"
    
    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", 
        "sqlite+aiosqlite:///./shodh.db"
    )
    
    # Judge & Runner Settings
    EXECUTION_MODE: str = os.getenv("EXECUTION_MODE", "SANDBOX_SUBPROCESS")  # REAL_DOCKER, SANDBOX_SUBPROCESS, TEST_DOUBLE
    DOCKER_IMAGE: str = os.getenv("DOCKER_IMAGE", "python:3.11-alpine")
    
    DEFAULT_TIME_LIMIT_MS: int = 2000
    DEFAULT_MEMORY_LIMIT_MB: int = 128
    DEFAULT_OUTPUT_LIMIT_BYTES: int = 65536  # 64 KB
    
    # Worker Queue & Leasing Settings
    WORKER_ID: str = os.getenv("WORKER_ID", "worker-default")
    WORKER_POLL_INTERVAL_SEC: float = 0.5
    WORKER_LEASE_SECONDS: int = 30
    WORKER_MAX_RETRIES: int = 3
    
    # Controllable Failure / Chaos testing
    INJECT_RUNNER_FAILURE: bool = os.getenv("INJECT_RUNNER_FAILURE", "false").lower() == "true"
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    class Config:
        env_file = ".env"
        extra = "allow"

settings = Settings()
