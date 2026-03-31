#!/usr/bin/env python3
"""
scan_worker.py — Background scan process.

Launched by app.py as a detached subprocess so the scan continues running
even after the browser tab is closed. Writes progress and partial results
to SQLite after every ticker so the UI can show live progress on reconnect.

Usage (internal — called by app.py, not by hand):
    python scan_worker.py <job_id>

The job row must already exist in the DB (created by create_scan_job()).
"""

import logging
import os
import sys

# Make sure we can import project modules from any working directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env before importing config so environment overrides take effect
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | scan_worker | %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: scan_worker.py <job_id>", file=sys.stderr)
        return 1

    job_id = int(sys.argv[1])

    from db import get_job, mark_job_running, update_job_progress, complete_job, fail_job, cancel_job
    from engine import analyze_ticker

    job = get_job(job_id)
    if not job:
        logger.error("Job %d not found in DB", job_id)
        return 1

    if job["status"] == "cancelled":
        logger.info("Job %d already cancelled — exiting", job_id)
        return 0

    # Apply the settings that were captured when the job was created
    config.MOCK_MODE = job["mock_mode"]

    tickers = job["tickers"]
    logger.info(
        "Starting job %d: %d tickers | mock=%s",
        job_id, len(tickers), config.MOCK_MODE,
    )

    mark_job_running(job_id)
    partial: list = []

    try:
        for i, ticker in enumerate(tickers):
            # Check for cancellation before each ticker
            fresh = get_job(job_id)
            if fresh and fresh["status"] == "cancelled":
                logger.info("Job %d cancelled at ticker %d/%d", job_id, i, len(tickers))
                return 0

            logger.info("[%d/%d] Analysing %s …", i + 1, len(tickers), ticker)

            # Write current_ticker *before* the fetch so progress is visible
            update_job_progress(job_id, done_count=i, current_ticker=ticker, partial_results=partial)

            report = analyze_ticker(ticker)
            partial.append(report)

            # Write updated partial results immediately after each ticker
            update_job_progress(job_id, done_count=i + 1, current_ticker=ticker, partial_results=partial)

        # Sort final results by score descending (mirrors run_engine behaviour)
        partial.sort(key=lambda r: (r.score, r.confidence), reverse=True)
        complete_job(job_id, partial)
        logger.info("Job %d complete", job_id)
        return 0

    except Exception as exc:
        logger.exception("Job %d crashed: %s", job_id, exc)
        fail_job(job_id, str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
