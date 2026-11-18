import asyncio
import subprocess
import sys
import json
import time
import os
import platform

def get_git_commit() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return "local-build"

def run_evaluation():
    print("=" * 75)
    print("      SHODH A CODE - FULL FUNCTIONAL EVALUATION SUITE")
    print("=" * 75)

    commit_hash = get_git_commit()
    exec_mode = os.getenv("EXECUTION_MODE", "SANDBOX_SUBPROCESS")
    data_version = "v1.0.0-deterministic-bundle"
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    print(f"Commit: {commit_hash} | Config: Production/Take-Home")
    print(f"Data Version: {data_version} | Execution Mode: {exec_mode}")
    print(f"Platform: {platform.platform()} | Python: {platform.python_version()}")
    print("-" * 75)

    scenario_families = [
        {"name": "Stage 1: Contest API, Auth & Tenancy", "file": "tests/test_stage1_contest_api.py"},
        {"name": "Stage 2: Asynchronous Judge & Safe Execution", "file": "tests/test_stage2_async_judge.py"},
        {"name": "Stage 3: Retries, Concurrency & Rejudging", "file": "tests/test_stage3_retries_concurrency.py"},
        {"name": "Stage 4: Security Boundaries & Diagnostics", "file": "tests/test_stage4_security_diagnostics.py"},
        {"name": "Stage 5: Comprehensive E2E Multi-Worker Lifecycle", "file": "tests/test_comprehensive_e2e.py"}
    ]

    all_passed = True
    results_summary = []

    for stage in scenario_families:
        print(f"Running: {stage['name']} ... ", end="", flush=True)
        t0 = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", stage["file"], "-q"],
            capture_output=True,
            text=True
        )
        elapsed = time.perf_counter() - t0
        passed = (proc.returncode == 0)
        status_str = "[PASS]" if passed else "[FAIL]"
        print(f"{status_str} ({elapsed:.2f}s)")

        if not passed:
            all_passed = False
            print("  Errors:")
            for line in proc.stdout.splitlines()[-10:]:
                print(f"    {line}")

        results_summary.append({
            "stage": stage["name"],
            "test_file": stage["file"],
            "passed": passed,
            "duration_sec": round(elapsed, 2)
        })

    print("-" * 75)
    verdict_text = "ALL VERIFICATION CHECKS PASSED - SUBMISSION READY" if all_passed else "SOME CHECKS FAILED"
    print(f"OVERALL EVALUATION VERDICT: {verdict_text}")
    print("=" * 75)

    evaluation_report = {
        "metadata": {
            "timestamp": timestamp,
            "commit": commit_hash,
            "configuration": "Production",
            "data_version": data_version,
            "execution_mode": exec_mode,
            "overall_status": "SUCCESS" if all_passed else "FAILURE"
        },
        "scenarios": results_summary
    }

    with open("evaluation_results.json", "w", encoding="utf-8") as f:
        json.dump(evaluation_report, f, indent=2)

    print("Saved evaluation results to evaluation_results.json\n")
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(run_evaluation())
