import time
import uuid
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db, AsyncSessionLocal
from app.seed.seed_data import seed_database
from app.api.auth_router import router as auth_router
from app.api.contest_router import router as contest_router
from app.api.problem_router import router as problem_router
from app.api.submission_router import router as submission_router
from app.api.leaderboard_router import router as leaderboard_router
from app.api.diagnostic_router import router as diagnostic_router

# Configure structured logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] [request_id=%(request_id)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()]
)

# Custom log filter to inject request_id
class RequestIdFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return True

for handler in logging.root.handlers:
    handler.addFilter(RequestIdFilter())

logger = logging.getLogger("app.main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database schema & ensure seed bundle exists
    logger.info("Initializing database and deterministic seed data...")
    await init_db()
    async with AsyncSessionLocal() as session:
        await seed_database(session)
    logger.info("Database initialization complete.")
    yield
    # Shutdown
    logger.info("Application shutting down.")

app = FastAPI(
    title="Shodh a Code - Contest Platform API",
    description=(
        "Production-grade backend for Shodh a Code contest platform.\n\n"
        "Features:\n"
        "- Authenticated identities, multi-tenancy, and role-based authorization.\n"
        "- Versioned problems and test cases with private test suite masking.\n"
        "- Asynchronous judging engine with sandboxing (bounded CPU, memory, timeout, output).\n"
        "- Transactional outbox, strict idempotency, bounded retries, and abandoned work recovery.\n"
        "- Deterministic leaderboard calculation, query plan optimization, and diagnostic logs."
    ),
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request ID & Audit Logging Middleware
@app.middleware("http")
async def request_id_and_logging_middleware(request: Request, call_next):
    req_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    request.state.request_id = req_id

    start_time = time.perf_counter()
    try:
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        response.headers["X-Request-Id"] = req_id
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
        return response
    except Exception as exc:
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        logger.error(f"Unhandled exception in request {req_id}: {exc}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected server error occurred.",
                "request_id": req_id
            },
            headers={"X-Request-Id": req_id}
        )

# HTTP Exception Handler (Unpack structured error dictionaries to top level)
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    req_id = getattr(request.state, "request_id", "-")
    if isinstance(exc.detail, dict):
        body = {
            "code": exc.detail.get("code", "ERROR"),
            "message": exc.detail.get("message", str(exc.detail)),
            "details": exc.detail.get("details", None),
            "request_id": req_id
        }
    else:
        body = {
            "code": "HTTP_ERROR",
            "message": str(exc.detail),
            "details": None,
            "request_id": req_id
        }
    return JSONResponse(status_code=exc.status_code, content=body, headers={"X-Request-Id": req_id})

# Validation Error Handler
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    req_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "code": "VALIDATION_ERROR",
            "message": "Input validation failed. Please check parameter constraints.",
            "details": exc.errors(),
            "request_id": req_id
        },
        headers={"X-Request-Id": req_id}
    )

# Mount API Routers
app.include_router(auth_router, prefix=settings.API_V1_PREFIX)
app.include_router(contest_router, prefix=settings.API_V1_PREFIX)
app.include_router(problem_router, prefix=settings.API_V1_PREFIX)
app.include_router(submission_router, prefix=settings.API_V1_PREFIX)
app.include_router(leaderboard_router, prefix=settings.API_V1_PREFIX)
app.include_router(diagnostic_router, prefix=settings.API_V1_PREFIX)

@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "execution_mode": settings.EXECUTION_MODE
    }
