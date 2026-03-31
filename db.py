"""
db.py — SQLite persistence for scan results and background job tracking.

Two tables:
    scans     — completed scan history (load instantly on app start)
    scan_jobs — in-progress background scan state (progress + partial results)

Usage:
    # Completed scans (written by scan_worker.py when done)
    from db import save_scan, load_latest_scan, list_scans, load_scan_by_id

    # Background job lifecycle (used by app.py + scan_worker.py)
    from db import create_scan_job, get_active_job, get_job
    # ... see scan_worker.py for update_job_progress / complete_job / fail_job
"""

import json
import logging
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

DB_PATH: str = os.getenv("ANNIE_DB_PATH", "annie.db")
MAX_STORED_SCANS: int = 10   # keep the 10 most recent scans, prune the rest


# ─────────────────────────────────────────────────────────────────────────────
# CONNECTION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at   TEXT    NOT NULL,
            ticker_count INTEGER NOT NULL,
            tickers      TEXT    NOT NULL,   -- JSON list of tickers that were scanned
            results      TEXT    NOT NULL,   -- JSON list of serialised StockReport dicts
            mock_mode    INTEGER NOT NULL DEFAULT 0,
            data_source  TEXT    NOT NULL DEFAULT 'yfinance'
        )
    """)
    # Background job table — one row per async scan run.
    # status lifecycle: pending → running → complete | error | cancelled
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scan_jobs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            status          TEXT    NOT NULL DEFAULT 'pending',
            created_at      TEXT    NOT NULL,
            updated_at      TEXT    NOT NULL,
            tickers         TEXT    NOT NULL,   -- JSON list of all tickers to scan
            ticker_count    INTEGER NOT NULL,
            done_count      INTEGER NOT NULL DEFAULT 0,
            current_ticker  TEXT,               -- ticker being processed right now
            partial_results TEXT,               -- JSON list, grows as each ticker completes
            mock_mode       INTEGER NOT NULL DEFAULT 0,
            data_source     TEXT    NOT NULL DEFAULT 'yfinance',
            error           TEXT
        )
    """)
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# SERIALISATION
# ─────────────────────────────────────────────────────────────────────────────

def _reports_to_dicts(reports) -> List[Dict[str, Any]]:
    """Convert List[StockReport] → list of plain dicts (JSON-safe)."""
    out = []
    for r in reports:
        d = asdict(r)
        out.append(d)
    return out


def _dicts_to_reports(rows: List[Dict[str, Any]]):
    """Reconstruct List[StockReport] from serialised dicts."""
    from engine import StockReport
    reports = []
    for d in rows:
        reports.append(StockReport(
            ticker          = d["ticker"],
            score           = d["score"],
            recommendation  = d["recommendation"],
            confidence      = d["confidence"],
            factor_scores   = d.get("factor_scores") or {},
            indicators      = d.get("indicators") or {},
            trade_plan      = d.get("trade_plan"),
            risk_note       = d.get("risk_note") or "",
            explanation     = d.get("explanation") or "",
            filters_passed  = d.get("filters_passed", True),
            filter_failures = d.get("filter_failures") or [],
            error           = d.get("error"),
        ))
    return reports


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def save_scan(reports, tickers: List[str]) -> None:
    """
    Persist a completed scan to SQLite.

    Args:
        reports: List[StockReport] returned by run_engine()
        tickers: The list of tickers that were requested (may differ from
                 reports if some failed silently)
    """
    if not reports:
        return

    try:
        conn = _connect()
        _ensure_schema(conn)

        conn.execute(
            """
            INSERT INTO scans (scanned_at, ticker_count, tickers, results, mock_mode, data_source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                len(tickers),
                json.dumps(tickers),
                json.dumps(_reports_to_dicts(reports)),
                int(config.MOCK_MODE),
                config.DATA_SOURCE,
            ),
        )

        # Prune old rows — keep only the MAX_STORED_SCANS most recent
        conn.execute(
            """
            DELETE FROM scans
            WHERE id NOT IN (
                SELECT id FROM scans ORDER BY id DESC LIMIT ?
            )
            """,
            (MAX_STORED_SCANS,),
        )

        conn.commit()
        conn.close()
        logger.info("Saved scan (%d results) to %s", len(reports), DB_PATH)

    except Exception as exc:
        logger.warning("Failed to save scan to DB: %s", exc)


def load_latest_scan() -> Optional[Dict[str, Any]]:
    """
    Load the most recent scan from SQLite.

    Returns:
        dict with keys:
            "reports"     — List[StockReport]
            "meta"        — dict: scanned_at, ticker_count, tickers, mock_mode, data_source
        or None if no scan has been saved yet.
    """
    try:
        if not os.path.exists(DB_PATH):
            return None

        conn = _connect()
        _ensure_schema(conn)

        row = conn.execute(
            "SELECT * FROM scans ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        if row is None:
            return None

        reports = _dicts_to_reports(json.loads(row["results"]))
        meta = {
            "scanned_at":   row["scanned_at"],
            "ticker_count": row["ticker_count"],
            "tickers":      json.loads(row["tickers"]),
            "mock_mode":    bool(row["mock_mode"]),
            "data_source":  row["data_source"],
        }
        logger.info(
            "Loaded cached scan from %s: %d results from %s",
            DB_PATH, len(reports), meta["scanned_at"],
        )
        return {"reports": reports, "meta": meta}

    except Exception as exc:
        logger.warning("Failed to load scan from DB: %s", exc)
        return None


def list_scans() -> List[Dict[str, Any]]:
    """
    Return metadata for all stored scans, newest first.
    Useful for a history view — does not load the full results blob.
    """
    try:
        if not os.path.exists(DB_PATH):
            return []

        conn = _connect()
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT id, scanned_at, ticker_count, tickers, mock_mode, data_source "
            "FROM scans ORDER BY id DESC"
        ).fetchall()
        conn.close()

        return [
            {
                "id":           r["id"],
                "scanned_at":   r["scanned_at"],
                "ticker_count": r["ticker_count"],
                "tickers":      json.loads(r["tickers"]),
                "mock_mode":    bool(r["mock_mode"]),
                "data_source":  r["data_source"],
            }
            for r in rows
        ]
    except Exception as exc:
        logger.warning("Failed to list scans from DB: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND JOB API
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def create_scan_job(tickers: List[str], mock_mode: bool, data_source: str) -> int:
    """
    Create a new scan job row and return its id.
    The worker process picks this up by id and updates it as it runs.
    """
    conn = _connect()
    _ensure_schema(conn)
    cur = conn.execute(
        """
        INSERT INTO scan_jobs
            (status, created_at, updated_at, tickers, ticker_count, mock_mode, data_source)
        VALUES ('pending', ?, ?, ?, ?, ?, ?)
        """,
        (_now(), _now(), json.dumps(tickers), len(tickers), int(mock_mode), data_source),
    )
    job_id = cur.lastrowid
    conn.commit()
    conn.close()
    logger.info("Created scan job %d (%d tickers)", job_id, len(tickers))
    return job_id


def get_job(job_id: int) -> Optional[Dict[str, Any]]:
    """Return the full state of a job row as a dict, or None."""
    try:
        conn = _connect()
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM scan_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        conn.close()
        if row is None:
            return None
        return _job_row_to_dict(row)
    except Exception as exc:
        logger.warning("get_job(%d) failed: %s", job_id, exc)
        return None


def get_active_job() -> Optional[Dict[str, Any]]:
    """
    Return the most recent job that is still pending or running, or None.
    Used by the UI to show progress on startup / tab switch.
    """
    try:
        if not os.path.exists(DB_PATH):
            return None
        conn = _connect()
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM scan_jobs WHERE status IN ('pending','running') "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row is None:
            return None
        return _job_row_to_dict(row)
    except Exception as exc:
        logger.warning("get_active_job() failed: %s", exc)
        return None


def mark_job_running(job_id: int) -> None:
    _job_update(job_id, "UPDATE scan_jobs SET status='running', updated_at=? WHERE id=?",
                (_now(), job_id))


def update_job_progress(
    job_id: int,
    done_count: int,
    current_ticker: str,
    partial_results,
) -> None:
    """Called by the worker after each ticker completes."""
    _job_update(
        job_id,
        """UPDATE scan_jobs
           SET done_count=?, current_ticker=?, partial_results=?, updated_at=?
           WHERE id=?""",
        (done_count, current_ticker,
         json.dumps(_reports_to_dicts(partial_results)), _now(), job_id),
    )


def complete_job(job_id: int, results) -> None:
    """Mark the job done and write final results. Also saves to the scans table."""
    try:
        conn = _connect()
        _ensure_schema(conn)

        # Read tickers + meta from the job row to save a scans history entry
        row = conn.execute("SELECT * FROM scan_jobs WHERE id=?", (job_id,)).fetchone()
        if row:
            tickers = json.loads(row["tickers"])
            results_json = json.dumps(_reports_to_dicts(results))

            conn.execute(
                """UPDATE scan_jobs
                   SET status='complete', done_count=ticker_count,
                       current_ticker=NULL, partial_results=?, updated_at=?
                   WHERE id=?""",
                (results_json, _now(), job_id),
            )

            # Also persist to the scans history table
            conn.execute(
                """INSERT INTO scans
                       (scanned_at, ticker_count, tickers, results, mock_mode, data_source)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (_now(), len(tickers), json.dumps(tickers), results_json,
                 row["mock_mode"], row["data_source"]),
            )
            conn.execute(
                "DELETE FROM scans WHERE id NOT IN "
                "(SELECT id FROM scans ORDER BY id DESC LIMIT ?)",
                (MAX_STORED_SCANS,),
            )

        conn.commit()
        conn.close()
        logger.info("Job %d complete (%d results)", job_id, len(results))
    except Exception as exc:
        logger.warning("complete_job(%d) failed: %s", job_id, exc)


def fail_job(job_id: int, error: str) -> None:
    _job_update(
        job_id,
        "UPDATE scan_jobs SET status='error', error=?, updated_at=? WHERE id=?",
        (error[:500], _now(), job_id),
    )
    logger.error("Job %d failed: %s", job_id, error)


def cancel_job(job_id: int) -> None:
    _job_update(
        job_id,
        "UPDATE scan_jobs SET status='cancelled', updated_at=? WHERE id=?",
        (_now(), job_id),
    )
    logger.info("Job %d cancelled", job_id)


def _job_update(job_id: int, sql: str, params: tuple) -> None:
    try:
        conn = _connect()
        _ensure_schema(conn)
        conn.execute(sql, params)
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("_job_update(%d) failed: %s", job_id, exc)


def _job_row_to_dict(row) -> Dict[str, Any]:
    partial_raw = row["partial_results"]
    partial = _dicts_to_reports(json.loads(partial_raw)) if partial_raw else []
    return {
        "id":             row["id"],
        "status":         row["status"],
        "created_at":     row["created_at"],
        "updated_at":     row["updated_at"],
        "tickers":        json.loads(row["tickers"]),
        "ticker_count":   row["ticker_count"],
        "done_count":     row["done_count"],
        "current_ticker": row["current_ticker"],
        "partial_results": partial,
        "mock_mode":      bool(row["mock_mode"]),
        "data_source":    row["data_source"],
        "error":          row["error"],
    }


def load_scan_by_id(scan_id: int) -> Optional[Dict[str, Any]]:
    """Load a specific scan by its database ID."""
    try:
        conn = _connect()
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM scans WHERE id = ?", (scan_id,)
        ).fetchone()
        conn.close()

        if row is None:
            return None

        return {
            "reports": _dicts_to_reports(json.loads(row["results"])),
            "meta": {
                "scanned_at":   row["scanned_at"],
                "ticker_count": row["ticker_count"],
                "tickers":      json.loads(row["tickers"]),
                "mock_mode":    bool(row["mock_mode"]),
                "data_source":  row["data_source"],
            },
        }
    except Exception as exc:
        logger.warning("Failed to load scan %d from DB: %s", scan_id, exc)
        return None
