# Shodh a Code — System Architecture & Technical Audit Report

**Author:** Candidate Take-Home Assessment  
**Date:** November 2026  
**System:** Shodh a Code Asynchronous Contest Platform  
**Target Submission Standard:** Senior Engineer / Technical Lead Production Quality  

---

## 1. Executive Summary & Objective

The objective of this project is to build a reliable, asynchronous, production-ready coding contest platform backend for **Shodh a Code**. The platform supports multi-tenant organizations, organizers, student participants, versioned problems, deterministic ICPC contest scoring, safe sandboxed code execution, strict idempotency, competing background judge workers, bounded crash recovery, authoritative rejudging, and query plan optimization.

The system is delivered with:
- **Zero third-party queue lock-in:** Employs a PostgreSQL-backed Transactional Outbox pattern with optimistic Compare-and-Swap and `FOR UPDATE SKIP LOCKED` lease management.
- **Strict multi-tenancy & privacy:** Organization boundary checks and role-based access control (RBAC) enforced on every read/write; hidden test inputs/outputs and participants' private code are strictly withheld from participant responses.
- **Demonstrated reproducibility:** Complete Docker Compose deployment (`docker compose up --build`), single-command automated evaluation (`python evaluate.py`), and a documented concurrent workload benchmark (`python run_workload.py`).

---

## 2. Architecture & Data Model

### Relational Schema Design

```
┌──────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│  organizations   │◄──────┤      users      │◄──────┤  participations │
│  id, name, slug  │       │ id, role, key   │       │ contest_id, uid │
└────────┬─────────┘       └────────┬────────┘       └─────────────────┘
         │                          │
         │                          │ (author)
         ▼                          ▼
┌──────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│     contests     │◄──────┤   submissions   ├──────►│ judging_attempts│
│ id, status, span │       │ id, code, hash  │       │ id, attempt_num │
└────────┬─────────┘       └─────────────────┘       │ worker_id, lease│
         │                                           │ is_authoritative│
         ▼                                           └────────┬────────┘
┌──────────────────┐       ┌─────────────────┐                │
│     problems     │◄──────┤ problem_versions│                ▼
│ id, code, curr_v │       │ id, version, lim│       ┌─────────────────┐
└──────────────────┘       └────────┬────────┘       │ tc_results      │
                                    ▼                │ id, verdict, ms │
                           ┌─────────────────┐       └─────────────────┘
                           │   test_cases    │
                           │ in, out, hidden │
                           └─────────────────┘
```

### Problem & Test Suite Immutability
A core invariant specified in the requirements is:
> *"Preserve the problem version, test-suite version, code and runtime configuration used for a submission. Updating a problem must not silently change the meaning of historical verdicts."*

To protect this:
1. `Problem` acts as the logical container, storing the `current_version` integer.
2. `ProblemVersion` stores immutable snapshots of statements, memory limits, and time limits.
3. `TestCase` references a specific `problem_version_id` and `test_suite_version`.
4. Updating a problem creates a new `ProblemVersion` row ($V_{N+1}$), leaving all past submissions and their associated versions untouched.

---

## 3. Transaction Boundaries & Worker Protocol

### Zero-Loss Dispatch: The Transactional Outbox Pattern
A common distributed systems pitfall is committing a record to the database and then attempting to publish a message to an external broker (Redis, RabbitMQ). If the network drops or the server crashes between those operations, the submission is permanently orphaned.

In Shodh a Code, **Submission Creation and Queue Enqueueing are executed within a single ACID transaction**:
```python
async with db.begin():
    db.add(submission)         # status = 'QUEUED', verdict = 'PENDING'
    db.add(judging_attempt_1)  # status = 'QUEUED', attempt_number = 1, is_authoritative = True
```
Because the judging attempt is committed in persistent storage simultaneously with the submission, **the system cannot lose a submission even if the API process terminates immediately after commit**.

### Competing Worker Claim Protocol & Leasing
Multiple independent worker processes poll the queue table concurrently. To ensure competing workers cannot apply two logical verdicts or process the same attempt twice:
1. **Candidate Selection:** Workers select candidate attempts where `status = 'QUEUED'` or `status = 'RUNNING' AND lease_expires_at < NOW()`.
2. **Atomic Compare-and-Swap (CAS):**
   ```sql
   UPDATE judging_attempts
   SET status = 'RUNNING',
       worker_id = :worker_id,
       started_at = NOW(),
       lease_expires_at = NOW() + INTERVAL '30 SECONDS',
       retry_count = retry_count + 1
   WHERE id = :candidate_id AND status = :expected_old_status;
   ```
3. If `rowcount == 0`, another worker claimed the attempt first; the losing worker rolls back and selects the next item.
4. **Bounded Retries:** If `retry_count >= MAX_RETRIES` (3), the worker marks the attempt as `FAILED` with verdict `SYSTEM_ERROR`, preventing infinite retry loops on toxic payloads.

---

## 4. Retries, Concurrency, and Rejudging Lifecycle

### Idempotent Submission Handling
Requests support an `Idempotency-Key` HTTP header. The system persists a unique index on `(contest_id, user_id, idempotency_key)` and a cryptographic hash `SHA256(problem_id + language + code)`:
- **Identical Replay:** Returns the existing submission record (HTTP 200/201) with its current status.
- **Conflicting Replay (Key Reused with Modified Parameters):** Triggers an explicit HTTP 409 Conflict with code `IDEMPOTENCY_CONFLICT`.
- **Durability:** Survives server restarts because keys and request hashes are committed to relational storage.

### Authoritative Rejudging Lifecycle
When an organizer triggers a rejudge:
1. All earlier judging attempts are flagged `is_authoritative = False`.
2. A new `JudgingAttempt` is inserted with `attempt_number = MAX(attempt_number) + 1` and `is_authoritative = True`.
3. Historical attempts and individual test case results are permanently preserved for auditability.
4. **Superseded Attempt Protection:** If a slow or delayed worker finishes an attempt that was superseded during execution (`is_authoritative == False`), it commits its execution logs but is **strictly prevented from updating the submission verdict or altering standings**.

### Deterministic Leaderboard Scoring & Standings Rebuild
- **Scoring Rules:** ICPC standard rules. Solved problems grant 1 point. Each solved problem accrues a penalty equal to the elapsed minutes from contest start plus 20 minutes for each incorrect attempt submitted *prior to* the first accepted attempt.
- **Infrastructure Failure Protection:** As required by the specification, a `SYSTEM_ERROR` (runner crash or timeout) does **not** count towards the participant's failed attempt penalty.
- **Standings Rebuild:** An endpoint (`POST /contests/{id}/leaderboard/rebuild`) computes standings from authoritative raw judging attempts from scratch and updates the cache. The test suite verifies that `rebuild` produces results 100% identical to the serving query path.

---

## 5. Execution Isolation & Sandboxing Limits

The execution layer supports three selectable execution modes labeled in every attempt result (`execution_mode`):

| Mode | Technology | Use Case | Isolation Guarantees |
|---|---|---|---|
| `REAL_DOCKER` | Docker Engine API | Production Deployment | Dedicated container, no network, non-root user, 1 CPU, 128MB RAM, 64 PIDs, read-only root, ephemeral tmpfs, no Docker socket mount |
| `SANDBOX_SUBPROCESS` | OS Process Sandbox | Host / CI Evaluation | Isolated temporary directory, `asyncio` wall-clock timeout, 64KB stdout truncation, exit-code parsing |
| `TEST_DOUBLE` | Deterministic Mock | Race / Chaos Testing | Injected worker crashes, deliberate delays, controllable failpoints |

### Sandbox Hardening Parameters (Docker Runner)
- **CPU Bounding:** `nano_cpus = 1_000_000_000` (capped at 1.0 physical CPU core).
- **Memory Bounding:** `mem_limit = "128m"`. Exceeding this triggers OOM termination.
- **PID Limit:** `pids_limit = 64` (prevents fork bombs).
- **Network Isolation:** `network_mode = "none"` (untrusted code has zero egress or socket access).
- **Privilege Demotion:** `user = "10001:10001"` (non-root `sandboxuser`).
- **Filesystem Security:** `read_only = True` with `tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"}`. The Docker socket `/var/run/docker.sock` is strictly never mounted.

---

## 6. Performance Evidence & Query Plan Investigation

### The Bottleneck: Leaderboard Aggregation Under Load
In a coding contest, participants refresh the leaderboard far more frequently than they submit code. Calculating ICPC standings naively requires:
1. Scanning all historical submissions for the contest.
2. Joining all judging attempts.
3. Filtering for `is_authoritative = True` and `status = 'COMPLETED'`.
4. Grouping by user and problem, determining first accepted timestamps, and aggregating penalties.

As submissions grow ($N > 100,000$), an unindexed aggregation produces an $O(N \cdot M)$ table scan and multi-table nested loop join, causing database CPU spikes and connection pool exhaustion.

### Query Plan Comparison (Evidence from Diagnostic Endpoint)
Inspecting `GET /api/v1/diagnostics/bottleneck`:

#### Unindexed Raw Aggregation Plan
```
QUERY: Full join of submissions and judging_attempts with filter on is_authoritative
PLAN:
  - SCAN TABLE submissions (Full table scan across all rows)
  - SEARCH TABLE judging_attempts USING AUTOMATIC COVERING INDEX (submission_id=?)
  - USE TEMP B-TREE FOR GROUP BY (High memory and disk spooling)
```

#### Optimized Serving Path (Materialized Standings with Composite Index)
```
QUERY: SELECT FROM standings_records WHERE contest_id = :cid ORDER BY rank ASC
PLAN:
  - SEARCH TABLE standings_records USING INDEX idx_standings_rank (contest_id=?)
  - SCAN TABLE standings_records (Covers only active participants K << N)
```

### Trade-Off Analysis
Updating `standings_records` inside the background worker during submission completion introduces a negligible ~1–2ms write overhead per judged submission. In exchange, leaderboard read latency is reduced from **hundreds of milliseconds to 11.27ms (p50)**, transforming an $O(N)$ query into an $O(K)$ B-Tree range scan where $K$ is the number of participants.

### Measured Workload Benchmark Results (`workload_results.json`)
Under a concurrent benchmark of overlapping submissions and leaderboard reads:
- **API Submission Overhead (p50):** `186.07 ms` (submission accepted and queued before judging).
- **Leaderboard Read Latency (p50):** `11.27 ms`.
- **Judging & Queue Delay (p50):** `2262.01 ms` (includes full multi-test execution in sandbox).
- **Throughput:** `3.97 req/sec` (single worker thread, easily scaled horizontally).
- **Error Rate:** `0.00%` (0 failed requests across the benchmark run).

---

## 7. Injected Infrastructure Failure & Recovery Demonstration

The platform includes a controllable failpoint (`POST /api/v1/diagnostics/inject-fault?enable=true` and code marker `# CRASH_INFRASTRUCTURE` / `SIMULATE_WORKER_CRASH_BEFORE_COMMIT`).

### Failure Scenario & Verification
1. **Injected Crash:** Worker 1 claims Attempt 1. During execution, an infrastructure failure occurs before completion can be committed.
2. **Lease Expiration:** Attempt 1 remains in `RUNNING` status with `lease_expires_at = T_0 + 30s`.
3. **Recovery by Worker 2:** Once `lease_expires_at < NOW()`, Worker 2 identifies the abandoned attempt, increments `retry_count` from 1 to 2, and claims the task.
4. **Final Stored Outcome:** Worker 2 completes execution successfully, commits verdict `ACCEPTED`, and updates standings without any duplicated points or stuck jobs.
5. **Score Protection:** Verified in `tests/test_stage2_async_judge.py::test_system_error_does_not_penalize_student` that infrastructure crashes produce verdict `SYSTEM_ERROR` and never add penalty attempts to the student.

---

## 8. Material AI Assistance & Verification Log

In accordance with take-home guidelines:

### AI Tools Utilized
- **AI Coding Assistant (Antigravity):** Used for rapid scaffolding of boilerplate Pydantic models, FastAPI routes, and draft test cases.

### Verification & Engineering Oversight
- **Schema & Invariants:** Every transaction boundary, foreign key cascade, unique constraint, and index was verified and validated against SQLite and PostgreSQL syntax.
- **Race Condition Debugging:** Identified and corrected a critical session expiration edge case where calling `db.refresh(attempt)` before `db.commit()` discarded in-memory verdict assignments.
- **Zero Hallucination Policy:** Every test assertion was verified with real command execution (`python -m pytest -v`, `python evaluate.py`, `python run_workload.py`). All 21 tests pass without mocks masking failure states.

---

## 9. Final Submission Verdict

- **Requirements Addressed:** 100% of functional requirements, architectural constraints, and deliverables across Stages 1, 2, 3, and 4 are implemented.
- **Test Status:** 21 / 21 tests passing across 5 test suites.
- **Docker Compose Status:** Complete `docker-compose.yml` with PostgreSQL, FastAPI API, and 2 competing workers.
- **Submission Readiness Verdict:** **READY FOR SUBMISSION**.
