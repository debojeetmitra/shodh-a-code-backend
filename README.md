# Shodh a Code — Coding Contest Platform Backend

[![Python Version](https://img.shields.io/badge/python-3.11%20%7C%203.13-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.163.2-009688.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791.svg)](https://www.postgresql.org/)
[![Docker Compose](https://img.shields.io/badge/Docker%20Compose-v2%2B-2496ED.svg)](https://docs.docker.com/compose/)
[![Test Suite](https://img.shields.io/badge/Tests-21%2F21%20Passing-brightgreen.svg)]()

Production-grade asynchronous contest judging backend built for **Shodh a Code**. Implements multi-tenancy, deterministic ICPC contest scoring, transactional outbox queueing, containerized sandboxed judging, strict idempotency, bounded retries, and reproducible verification across 5 scenario families.

---

## 1. Quick Start (Reproducibility)

### Option A: Complete System via Docker Compose (Single Command)

Starts PostgreSQL 16, FastAPI REST API, and two competing background Judge Workers (`worker-1`, `worker-2`), with the database schema and deterministic seed bundle initialized automatically:

```bash
docker compose up --build
```

- **Interactive API Documentation (Swagger / OpenAPI):** [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc Documentation:** [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **Health Check:** [http://localhost:8000/health](http://localhost:8000/health)

### Option B: Local Python Execution

```bash
# 1. Install pinned dependencies
pip install -r requirements.txt

# 2. Seed database with deterministic bundle (Orgs, Users, Contests, Problems)
python -m app.seed.seed_data

# 3. Start API Server (in Terminal 1)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 4. Start Background Judge Worker (in Terminal 2)
python worker_daemon.py --worker-id worker-local-1
```

---

## 2. Evaluation & Verification Commands

The assessment requires one-command functional evaluation and documented workload benchmarking:

### Single Command for Functional Evaluation
Runs the complete 21-test suite across all 5 assessment scenario families, asserts persistent store states, and saves `evaluation_results.json`:

```bash
python evaluate.py
```

### Documented Concurrent Workload Command
Executes a repeatable workload of overlapping submissions and leaderboard reads with configurable concurrency and request mixes, reporting API latency percentiles (p50, p95, p99), throughput (RPS), error rates, and judging queue delays to `workload_results.json`:

```bash
# Run 50 requests with concurrency of 5 and 40% submission / 60% read mix
python run_workload.py --requests 50 --concurrency 5 --submission-ratio 0.4
```

---

## 3. Architecture & Data Model

```
       [ Client HTTP Requests ]
                  │
                  ▼
         [ FastAPI REST API ]
                  │ (Single Atomic Transaction)
                  ├───► Persists Submission (QUEUED, PENDING)
                  └───► Persists Initial JudgingAttempt 1 (QUEUED, is_authoritative=True)
                              │
                    [ Transactional Outbox ]
                              │
         ┌────────────────────┴────────────────────┐
         ▼                                         ▼
[ Worker 1 (SELECT FOR UPDATE) ]         [ Worker 2 (SELECT FOR UPDATE) ]
  • Compare-and-Swap claim                 • Compare-and-Swap claim
  • Lease heartbeat & 30s timeout          • Lease heartbeat & 30s timeout
  • Sandboxed Container Execution          • Sandboxed Container Execution
  • Atomic Verdict & Score Update          • Atomic Verdict & Score Update
         └────────────────────┬────────────────────┘
                              ▼
                [ Standings Cache & Leaderboard ]
```

### Transaction Boundaries & Invariant Protection
1. **Zero-Loss Queue Dispatch (Transactional Outbox):** When a participant submits code, `Submission` and `JudgingAttempt` (Attempt 1) are inserted in the **same database transaction**. No submission can ever be committed without being queued, and no external message queue disconnect can drop a submission.
2. **Competing Worker Exclusion:** Workers query candidates using `SELECT ... FOR UPDATE SKIP LOCKED` (PostgreSQL) and commit an atomic Compare-and-Swap (`UPDATE judging_attempts SET status='RUNNING', worker_id=:wid WHERE id=:id AND status='QUEUED'`). Exactly one worker claims the attempt; any competing worker observes `rowcount == 0` and backs off.
3. **Optimistic Versioning & Rejudging Safety:** Rejudging creates Attempt $N+1$ marked `is_authoritative=True` and marks earlier attempts `is_authoritative=False`. When an old worker finishes judging a superseded attempt, it records its test results but is barred from updating `submission.verdict` or standings.

---

## 4. State Model & Contracts

### Submission & Judging Attempt Lifecycle

```
[QUEUED] ──► [RUNNING] ──► [COMPLETED] (Verdict: AC, WA, RE, CE, TLE, OLE)
    │           │
    │           └──► (Crash/Timeout) ──► [RUNNING] (Lease Expired) ──► Recovered by Worker 2
    │                                                                          │
    │                                                                   (Exceeded Max Retries)
    │                                                                          ▼
    └───────────────────────────────────────────────────────────────► [FAILED] (Verdict: SYSTEM_ERROR)
```

### Verdicts Supported
- `ACCEPTED`: Output matches expected test case output (normalized trailing whitespace).
- `WRONG_ANSWER`: Program exited 0 but output did not match expected output.
- `RUNTIME_ERROR`: Unhandled exception, segmentation fault, or non-zero return code.
- `COMPILATION_ERROR`: Python syntax or indentation error during parsing.
- `TIME_LIMIT_EXCEEDED`: Wall-clock execution exceeded `time_limit_ms`.
- `OUTPUT_LIMIT_EXCEEDED`: Program exceeded 64 KB stdout bound.
- `SYSTEM_ERROR`: Judge infrastructure failure or worker crash. **Does not reduce student score or add penalty points.**

---

## 5. Sample Seeded Identities & Authentication

Authentication is performed via `Authorization: Bearer <token>` or `X-API-Key: <token>` or `X-User-Id: <user_id>`.

| Identity Role | Username | Organization | API Key / Token | Access Scope |
|---|---|---|---|---|
| **Organizer (Org Alpha)** | `alice_organizer` | Alpha Institute of Technology | `sec_alpha_organizer_key_9999` | Full manage Alpha contests, problems, rejudge |
| **Participant 1 (Org Alpha)** | `bob_coder` | Alpha Institute of Technology | `sec_alpha_student1_key_8888` | Submit to Alpha contests, inspect own code |
| **Participant 2 (Org Alpha)** | `charlie_coder` | Alpha Institute of Technology | `sec_alpha_student2_key_7777` | Submit to Alpha contests, inspect own code |
| **Organizer (Org Beta)** | `diana_organizer` | Beta National University | `sec_beta_organizer_key_6666` | Full manage Beta contests and problems |
| **Participant 1 (Org Beta)** | `eve_hacker` | Beta National University | `sec_beta_student1_key_5555` | Scoped strictly to Beta organization |

---

## 6. API Endpoints Overview

| Method | Endpoint | Role | Description |
|---|---|---|---|
| `GET` | `/api/v1/auth/me` | Any | Returns authenticated user profile and organization |
| `GET` | `/api/v1/auth/fixtures` | Public | Lists test fixtures and sample API keys |
| `POST` | `/api/v1/contests` | Organizer | Creates a new contest scoped to organization |
| `GET` | `/api/v1/contests` | Any | Paginated list of organization contests |
| `POST` | `/api/v1/contests/{id}/join` | Any | Registers student for a contest |
| `POST` | `/api/v1/problems` | Organizer | Creates problem + version 1 + test cases |
| `POST` | `/api/v1/problems/{id}/versions` | Organizer | Creates immutable new problem version |
| `GET` | `/api/v1/problems/{id}` | Any | Retrieves problem statement (hidden test cases masked for students) |
| `POST` | `/api/v1/contests/{id}/submissions` | Participant | Submits code (supports `Idempotency-Key` header) |
| `GET` | `/api/v1/contests/{id}/submissions` | Any | Lists contest submissions (scoped by role) |
| `GET` | `/api/v1/submissions/{id}` | Author/Org | Detailed submission view (masks private code & hidden I/O) |
| `POST` | `/api/v1/contests/{cid}/submissions/{sid}/rejudge` | Organizer | Rejudges submission against problem version |
| `GET` | `/api/v1/contests/{id}/leaderboard` | Any | Retrieves deterministic ICPC standings |
| `POST` | `/api/v1/contests/{id}/leaderboard/rebuild` | Organizer | Rebuilds standings from authoritative records from scratch |
| `GET` | `/api/v1/diagnostics/bottleneck` | Organizer | Query plan comparison (unindexed vs indexed serving path) |
| `POST` | `/api/v1/diagnostics/inject-fault` | Organizer | Injects controllable runner infrastructure failure |

---

## 7. Submission Report

A comprehensive four-page technical audit report is available in **[`SUBMISSION_REPORT.md`](SUBMISSION_REPORT.md)** detailing:
1. Architecture, schema, and isolation boundaries.
2. Transaction isolation and multi-process concurrency guarantees.
3. Idempotency, crash recovery, and authoritative rejudging protocol.
4. Measured query plan bottleneck analysis and trade-offs.
5. Injected infrastructure failure recovery demonstration.
6. Material AI assistance disclosure and verification log.
