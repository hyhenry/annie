"""
db.py — SQLite persistence for scan results.

Stores the output of run_engine() so the Streamlit UI can load the last scan
instantly on startup without re-fetching market data.

Schema:
    scans(id, scanned_at, ticker_count, tickers, results, mock_mode, data_source)

Only the most recent MAX_STORED_SCANS scans are kept — older rows are pruned
automatically after each save.

Usage:
    from db import save_scan, load_latest_scan

    # After running the engine:
    save_scan(reports, tickers_used)

    # On app startup:
    row = load_latest_scan()
    if row:
        reports  = row["reports"]       # List[StockReport]
        meta     = row["meta"]          # dict with scanned_at, ticker_count, etc.
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
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at  TEXT    NOT NULL,
            ticker_count INTEGER NOT NULL,
            tickers     TEXT    NOT NULL,   -- JSON list of tickers that were scanned
            results     TEXT    NOT NULL,   -- JSON list of serialised StockReport dicts
            mock_mode   INTEGER NOT NULL DEFAULT 0,
            data_source TEXT    NOT NULL DEFAULT 'yfinance'
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
