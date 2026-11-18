import asyncio
import argparse
import logging
import signal
import sys
from app.judge.worker import JudgeWorker
from app.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [worker=%(name)s] %(message)s"
)
logger = logging.getLogger("worker_daemon")

def main():
    parser = argparse.ArgumentParser(description="Shodh a Code - Background Judge Worker Daemon")
    parser.add_argument("--worker-id", type=str, default="worker-1", help="Unique identifier for this worker instance")
    parser.add_argument("--poll-interval", type=float, default=0.5, help="Poll interval in seconds")
    parser.add_argument("--lease-seconds", type=int, default=30, help="Attempt lease duration before work considered abandoned")
    parser.add_argument("--max-retries", type=int, default=3, help="Max retry attempts for crashed jobs")
    parser.add_argument("--runner-mode", type=str, default=None, help="Execution mode override: REAL_DOCKER, SANDBOX_SUBPROCESS, TEST_DOUBLE")
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit")
    args = parser.parse_args()

    worker = JudgeWorker(
        worker_id=args.worker_id,
        lease_seconds=args.lease_seconds,
        max_retries=args.max_retries,
        runner_mode=args.runner_mode
    )

    async def run():
        await init_db()
        if args.once:
            logger.info(f"Worker {args.worker_id} running single job check...")
            processed = await worker.run_once()
            logger.info(f"Worker {args.worker_id} completed single pass (processed={processed}).")
        else:
            await worker.run_loop()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info(f"Worker {args.worker_id} gracefully shut down.")

if __name__ == "__main__":
    main()
