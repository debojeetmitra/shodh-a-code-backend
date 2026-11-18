import asyncio
import time
import argparse
import json
import statistics
import platform
import psutil
from typing import List, Dict, Any
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.database import init_db, AsyncSessionLocal
from app.seed.seed_data import seed_database, SEED_SAMPLE_PROGRAMS
from app.auth.fixtures import FIXTURE_USERS
from app.judge.worker import JudgeWorker

async def run_workload(
    num_requests: int = 50,
    concurrency: int = 5,
    submission_ratio: float = 0.4,
    execution_mode: str = "SANDBOX_SUBPROCESS"
) -> Dict[str, Any]:
    print("=" * 70)
    print("SHODH A CODE - CONCURRENT WORKLOAD BENCHMARK")
    print(f"Total Requests: {num_requests} | Concurrency: {concurrency}")
    print(f"Request Mix: {int(submission_ratio*100)}% Submissions, {int((1-submission_ratio)*100)}% Leaderboard Reads")
    print(f"Execution Mode: {execution_mode}")
    print("=" * 70)

    # Initialize DB & Seed
    await init_db()
    async with AsyncSessionLocal() as session:
        await seed_database(session)

    # Launch background worker
    worker = JudgeWorker(worker_id="benchmark-worker", runner_mode=execution_mode)
    worker_task = asyncio.create_task(worker.run_loop())

    transport = ASGITransport(app=app)
    contest_id = "c1111111-1111-1111-1111-111111111111"
    prob_id = "p1111111-1111-1111-1111-111111111111"

    student_keys = ["alpha_student_1", "alpha_student_2"]
    
    submission_latencies = []
    leaderboard_latencies = []
    queue_delays = []
    errors = 0

    semaphore = asyncio.Semaphore(concurrency)

    async def execute_task(req_idx: int):
        nonlocal errors
        async with semaphore:
            u_key = student_keys[req_idx % len(student_keys)]
            user = FIXTURE_USERS[u_key]
            headers = {
                "Authorization": f"Bearer {user['api_key']}",
                "X-User-Id": user["id"]
            }

            is_submission = (req_idx % 10) < int(submission_ratio * 10)

            async with AsyncClient(transport=transport, base_url="http://benchmark") as client:
                if is_submission:
                    # Measure API Overhead (POST to 201 before judging)
                    t0 = time.perf_counter()
                    try:
                        res = await client.post(
                            f"/api/v1/contests/{contest_id}/submissions",
                            json={
                                "problem_id": prob_id,
                                "code": SEED_SAMPLE_PROGRAMS["SUM2"]["correct"],
                                "language": "python"
                            },
                            headers=headers
                        )
                        api_lat_ms = (time.perf_counter() - t0) * 1000.0
                        if res.status_code == 201:
                            submission_latencies.append(api_lat_ms)
                            sub_id = res.json()["id"]

                            # Track queue delay until completed
                            poll_start = time.perf_counter()
                            for _ in range(50):
                                await asyncio.sleep(0.1)
                                sub_check = await client.get(f"/api/v1/submissions/{sub_id}", headers=headers)
                                if sub_check.status_code == 200 and sub_check.json()["status"] == "COMPLETED":
                                    queue_delays.append((time.perf_counter() - poll_start) * 1000.0)
                                    break
                        else:
                            errors += 1
                    except Exception:
                        errors += 1
                else:
                    # Measure Leaderboard Read Latency
                    t0 = time.perf_counter()
                    try:
                        res = await client.get(
                            f"/api/v1/contests/{contest_id}/leaderboard",
                            headers=headers
                        )
                        api_lat_ms = (time.perf_counter() - t0) * 1000.0
                        if res.status_code == 200:
                            leaderboard_latencies.append(api_lat_ms)
                        else:
                            errors += 1
                    except Exception:
                        errors += 1

    overall_start = time.perf_counter()
    tasks = [asyncio.create_task(execute_task(i)) for i in range(num_requests)]
    await asyncio.gather(*tasks)
    total_time_sec = time.perf_counter() - overall_start

    # Stop worker
    worker.running = False
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

    def calc_percentiles(data: List[float]) -> Dict[str, float]:
        if not data:
            return {"count": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "avg": 0.0, "min": 0.0, "max": 0.0}
        s = sorted(data)
        return {
            "count": len(s),
            "p50": round(statistics.median(s), 2),
            "p95": round(s[int(0.95 * len(s))], 2) if len(s) >= 20 else round(s[-1], 2),
            "p99": round(s[int(0.99 * len(s))], 2) if len(s) >= 100 else round(s[-1], 2),
            "avg": round(statistics.mean(s), 2),
            "min": round(min(s), 2),
            "max": round(max(s), 2)
        }

    sub_stats = calc_percentiles(submission_latencies)
    lb_stats = calc_percentiles(leaderboard_latencies)
    queue_stats = calc_percentiles(queue_delays)

    throughput_rps = round(num_requests / total_time_sec, 2)

    results = {
        "workload_metadata": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "os": platform.platform(),
            "cpu_count": psutil.cpu_count(logical=True),
            "total_ram_gb": round(psutil.virtual_memory().total / (1024**3), 2),
            "execution_mode": execution_mode,
            "total_requests": num_requests,
            "concurrency": concurrency,
            "duration_seconds": round(total_time_sec, 2),
            "throughput_rps": throughput_rps,
            "errors": errors
        },
        "api_submission_overhead_ms": sub_stats,
        "leaderboard_read_latency_ms": lb_stats,
        "async_judge_queue_delay_ms": queue_stats
    }

    # Print Report
    print("\n" + "=" * 70)
    print("WORKLOAD RESULTS SUMMARY")
    print("=" * 70)
    print(f"Total Completed: {num_requests - errors}/{num_requests} | Errors: {errors}")
    print(f"Total Time: {total_time_sec:.2f}s | Throughput: {throughput_rps} req/sec")
    print("-" * 70)
    print(f"API Submission Overhead (p50: {sub_stats['p50']}ms | p95: {sub_stats['p95']}ms | avg: {sub_stats['avg']}ms)")
    print(f"Leaderboard Read Latency (p50: {lb_stats['p50']}ms | p95: {lb_stats['p95']}ms | avg: {lb_stats['avg']}ms)")
    print(f"Queue & Judging Delay   (p50: {queue_stats['p50']}ms | p95: {queue_stats['p95']}ms | avg: {queue_stats['avg']}ms)")
    print("=" * 70)

    with open("workload_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print("Results saved to workload_results.json")

    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run concurrent workload benchmark")
    parser.add_argument("--requests", type=int, default=50, help="Total number of requests")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent workers")
    parser.add_argument("--submission-ratio", type=float, default=0.4, help="Ratio of submission requests")
    parser.add_argument("--execution-mode", type=str, default="SANDBOX_SUBPROCESS", help="REAL_DOCKER, SANDBOX_SUBPROCESS, TEST_DOUBLE")
    args = parser.parse_args()

    asyncio.run(run_workload(
        num_requests=args.requests,
        concurrency=args.concurrency,
        submission_ratio=args.submission_ratio,
        execution_mode=args.execution_mode
    ))
