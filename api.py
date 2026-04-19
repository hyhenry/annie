"""
api.py — FastAPI backend for Annie.

Exposes all engine capabilities as REST endpoints and serves the React
frontend as static files in production.

Run (development):
    uvicorn api:app --reload --port 8000

Run (production):
    uvicorn api:app --host 0.0.0.0 --port 8000
"""

import json
import logging
import math
import pathlib
import subprocess
import sys
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import config
from db import (
    get_job,
    get_active_job,
    cleanup_stale_jobs,
    cancel_job,
    create_scan_job,
    load_latest_scan,
    load_scan_by_id,
    list_scans,
    save_portfolio_results,
    load_portfolio_results,
)
from engine import load_tickers
from portfolio import load_portfolio, save_portfolio, Portfolio, Holding
from portfolio_monitor import monitor_portfolio
from universe import get_sectors, get_tickers_for_sectors

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Annie API", version="1.0")


@app.on_event("startup")
def on_startup() -> None:
    cleanup_stale_jobs()


# Allow the Vite dev server to call the API without CORS issues
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _sanitize(obj: Any) -> Any:
    """Recursively replace NaN/Inf floats with None for JSON safety."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def _to_dict(obj) -> Any:
    """Recursively convert dataclass instances to dicts, sanitizing floats."""
    if hasattr(obj, "__dataclass_fields__"):
        return _sanitize(asdict(obj))
    return obj


def _launch_worker(job_id: int) -> None:
    worker = pathlib.Path(__file__).parent / "scan_worker.py"
    subprocess.Popen(
        [sys.executable, str(worker), str(job_id)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _job_response(job: dict) -> dict:
    return {
        "id":             job["id"],
        "status":         job["status"],
        "ticker_count":   job["ticker_count"],
        "done_count":     job["done_count"],
        "current_ticker": job["current_ticker"],
        "mock_mode":      job["mock_mode"],
        "error":          job["error"],
        "created_at":     job["created_at"],
        "updated_at":     job["updated_at"],
        "partial_results": [_sanitize(asdict(r)) for r in (job["partial_results"] or [])],
    }


# ─────────────────────────────────────────────────────────────────────────────
# REQUEST MODELS
# ─────────────────────────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    tickers: List[str]
    mock_mode: Optional[bool] = None


class HoldingIn(BaseModel):
    ticker:           str
    shares:           float
    avg_cost:         float
    entry_date:       str
    thesis:           str = ""
    stop_loss:        Optional[float] = None
    take_profit:      Optional[float] = None
    peak_price:       Optional[float] = None
    trailing_stop_pct: float = config.TRAILING_STOP_DEFAULT_PCT


class PortfolioIn(BaseModel):
    account_size: float = config.ACCOUNT_SIZE_USD
    holdings: List[HoldingIn]


class TickersRequest(BaseModel):
    sectors: List[str] = []


class MockToggle(BaseModel):
    enabled: bool


# ─────────────────────────────────────────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok"}


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/config")
def get_config():
    return {
        "mock_mode":                  config.MOCK_MODE,
        "risk_per_trade_usd":         config.RISK_PER_TRADE_USD,
        "account_size_usd":           config.ACCOUNT_SIZE_USD,
        "atr_stop_loss_multiplier":   config.ATR_STOP_LOSS_MULTIPLIER,
        "atr_take_profit_multiplier": config.ATR_TAKE_PROFIT_MULTIPLIER,
        "trailing_stop_default_pct":  config.TRAILING_STOP_DEFAULT_PCT,
        "max_position_pct":           config.MAX_POSITION_PCT,
        "intervals":                  config.INTERVALS,
        "recommendation_bands":       config.RECOMMENDATION_BANDS,
        "factor_weights":             config.FACTOR_WEIGHTS,
        "hard_filters":               config.HARD_FILTERS,
    }


@app.post("/api/config/mock")
def set_mock_mode(body: MockToggle):
    config.MOCK_MODE = body.enabled
    return {"mock_mode": config.MOCK_MODE}


# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/portfolio")
def get_portfolio():
    portfolio = load_portfolio(config.PORTFOLIO_FILE)
    return {
        "account_size": portfolio.account_size,
        "holdings":     [_sanitize(asdict(h)) for h in portfolio.holdings],
    }


@app.put("/api/portfolio")
def update_portfolio(data: PortfolioIn):
    holdings = [
        Holding(
            ticker=h.ticker.upper().strip(),
            shares=h.shares,
            avg_cost=h.avg_cost,
            entry_date=h.entry_date,
            thesis=h.thesis,
            stop_loss=h.stop_loss,
            take_profit=h.take_profit,
            peak_price=h.peak_price,
            trailing_stop_pct=h.trailing_stop_pct,
        )
        for h in data.holdings
    ]
    portfolio = Portfolio(holdings=holdings, account_size=data.account_size)
    save_portfolio(portfolio, config.PORTFOLIO_FILE)
    return {"saved": True, "holding_count": len(holdings)}


@app.post("/api/portfolio/analyze")
def analyze_portfolio(mock_mode: Optional[bool] = None):
    original = config.MOCK_MODE
    if mock_mode is not None:
        config.MOCK_MODE = mock_mode
    try:
        portfolio = load_portfolio(config.PORTFOLIO_FILE)
        if not portfolio.holdings:
            return {"reports": [], "analyzed_at": None}

        reports = monitor_portfolio(portfolio, update_peaks=True)
        save_portfolio(portfolio, config.PORTFOLIO_FILE)   # persist updated peaks

        reports_dicts = [_sanitize(asdict(r)) for r in reports]
        analyzed_at = reports[0].analysed_at if reports else None
        save_portfolio_results(reports_dicts, config.MOCK_MODE, analyzed_at)

        return {"reports": reports_dicts, "analyzed_at": analyzed_at}
    except Exception as exc:
        logger.exception("Portfolio analysis failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        config.MOCK_MODE = original


@app.get("/api/portfolio/results")
def get_portfolio_results():
    data = load_portfolio_results()
    if data is None:
        return {"reports": [], "analyzed_at": None}
    return data


# ─────────────────────────────────────────────────────────────────────────────
# SCANNER
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/scan/active")
def get_active_scan():
    job = get_active_job()
    return _job_response(job) if job else None


@app.get("/api/scan/history")
def get_scan_history():
    return list_scans()


@app.get("/api/scan/latest")
def get_latest_scan():
    data = load_latest_scan()
    if data is None:
        return None
    return {
        "meta":    data["meta"],
        "reports": [_sanitize(asdict(r)) for r in data["reports"]],
    }


@app.get("/api/scan/{scan_id}")
def get_scan(scan_id: int):
    data = load_scan_by_id(scan_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    return {
        "meta":    data["meta"],
        "reports": [_sanitize(asdict(r)) for r in data["reports"]],
    }


@app.post("/api/scan")
def start_scan(req: ScanRequest):
    tickers = [t.upper().strip() for t in req.tickers if t.strip()]
    if not tickers:
        raise HTTPException(status_code=400, detail="No tickers provided")
    mock = req.mock_mode if req.mock_mode is not None else config.MOCK_MODE
    job_id = create_scan_job(tickers, mock, "yfinance")
    _launch_worker(job_id)
    return {"job_id": job_id}


@app.get("/api/scan/job/{job_id}")
def get_job_status(job_id: int):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_response(job)


@app.delete("/api/scan/job/{job_id}")
def cancel_scan(job_id: int):
    cancel_job(job_id)
    return {"cancelled": True}


@app.get("/api/tickers")
def get_default_tickers():
    return {"tickers": load_tickers()}


# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSE
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/universe/sectors")
def universe_sectors():
    try:
        return {"sectors": get_sectors()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/universe/tickers")
def universe_tickers(req: TickersRequest):
    try:
        tickers = get_tickers_for_sectors(req.sectors)
        return {"tickers": tickers, "count": len(tickers)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# SERVE REACT APP (production build)
# ─────────────────────────────────────────────────────────────────────────────

_DIST = pathlib.Path(__file__).parent / "frontend" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="static")
