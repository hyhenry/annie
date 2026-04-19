# Annie — Claude Context

## What this project is
A multifactor swing-trade scoring and portfolio monitoring engine. Fetches market data, computes technical indicators, and produces Buy/Watch/Avoid scores (scanner) and Hold/Trim/Sell recommendations (portfolio monitor). React + FastAPI web UI + argparse CLI.

## Running the app
```bash
# Web UI — production (build React first, then serve from FastAPI)
cd frontend && npm run build && cd ..
uvicorn api:app --port 8000

# Web UI — development (hot-reload React on :5173, FastAPI on :8000)
uvicorn api:app --reload --port 8000 &
cd frontend && npm run dev

# CLI scanner
python main.py --tickers AAPL,MSFT,NVDA
python main.py --mock --all --only-buy --top 10
python main.py --help

# Tests
python -m pytest tests/ -v
```

## File map
| File | Role |
|---|---|
| `config.py` | Single source of truth for all weights, thresholds, settings |
| `market_data.py` | Data fetching: yfinance + local `ta` indicators. Returns `RawIndicatorBundle` |
| `indicators.py` | Parses `RawIndicatorBundle` → `IndicatorData`; normalises each indicator 0-100 |
| `scoring.py` | Weighted factor scores → Opportunity Score + Buy/Watch/Avoid |
| `risk.py` | ATR-based stop-loss, take-profit, position sizing → `TradePlan` |
| `engine.py` | Orchestrates scanner pipeline per ticker; returns `StockReport` list |
| `main.py` | CLI entry point (argparse, JSON to stdout) |
| `portfolio.py` | `Holding` / `Portfolio` dataclasses; JSON read/write |
| `hold_scoring.py` | Hold Quality Score + Sell Risk Score models |
| `portfolio_monitor.py` | Orchestrates portfolio analysis; returns `HoldingReport` list |
| `universe.py` | S&P 500 + NASDAQ 100 universe from Wikipedia; sector/industry metadata; 7-day SQLite cache |
| `scan_worker.py` | Detached subprocess: runs scans async, writes progress to SQLite per ticker |
| `db.py` | SQLite persistence: `scans` history table + `scan_jobs` live progress table |
| `api.py` | FastAPI REST backend — all engine capabilities as JSON endpoints |
| `frontend/` | React + TypeScript + Vite + Tailwind UI — built to `frontend/dist/` |
| `portfolio.json` | User's holdings (edited via Manage tab or directly) |
| `tickers.json` | Default short ticker list for Scanner tab custom mode |
| `tests/test_scoring.py` | 83 unit tests — all passing |

## Architecture decisions

**Data source:** `yfinance` + `ta` library — free, no API key, computes all indicators locally from OHLCV data.

**Async scans:** Clicking "Run Scan" spawns `scan_worker.py` as a detached subprocess (`start_new_session=True`) so it survives browser close. Worker writes `done_count`, `current_ticker`, and partial results to `scan_jobs` table after every ticker. App polls DB every 3s and shows progress bar + partial results table + ETA. Cancel button sets `status='cancelled'`; worker checks before each ticker.

**Universe / sector scanner:** `universe.py` downloads S&P 500 from Wikipedia (`pd.read_html`, `id="constituents"`) and NASDAQ 100 from the Nasdaq-100 article. Combined (S&P 500 preferred on overlap), deduplicated, cached in `universe` + `universe_meta` SQLite tables with 7-day TTL. Scanner tab has two modes: "Custom tickers" (text input) and "By sector" (multiselect from live sector list → tickers derived from universe).

**SQLite schema:**
- `scans` — completed scan history (up to 10 rows, pruned automatically). Loaded on app startup for instant display.
- `scan_jobs` — one row per async run with `status` (pending→running→complete|error|cancelled), `done_count`, `current_ticker`, `partial_results` JSON.

**Scoring models:**
- *Opportunity Score* (scanner): 7 weighted factors — trend 25%, momentum 20%, volume 15%, entry timing 15%, volatility 10%, multi-timeframe 10%, risk penalty 5%. Buy ≥80, Watch ≥60, Avoid <60.
- *Hold Quality Score* (portfolio): trend integrity 30%, momentum health 25%, volume support 20%, technical position 15%, multi-timeframe 10%.
- *Sell Risk Score* (portfolio): additive penalties. Sell ≥70, Trim ≥50, Watch Closely ≥30, Hold <30.

**4h timeframe:** yfinance has no native 4h stock bars. We download 1h bars and resample with pandas `.resample("4h").agg(OHLCV)`.

**Mock data:** 6 pre-built scenarios in `market_data._MOCK_DATA` — AAPL (Buy ~85), MSFT (Watch ~80), NVDA (Watch ~78), TSLA (Watch/Trim), AMC (Avoid/Sell), SNDL (auto-Avoid, price filter). All other tickers in mock mode get a neutral fallback (Watch ~50).

## Key config knobs (config.py / .env)
| Setting | Default | Effect |
|---|---|---|
| `MOCK_MODE` | `false` | `true` for offline sample data |
| `RISK_PER_TRADE_USD` | `500` | Position sizing budget per trade |
| `TRAILING_STOP_DEFAULT_PCT` | `0.08` | Default trailing stop (8%) |
| `MAX_POSITION_PCT` | `0.15` | Concentration limit (15% of portfolio) |
| `ANNIE_DB_PATH` | `annie.db` | SQLite file location |

## Known NaN / robustness fixes (implemented)
- **NaN sanitization** — `db.py` and `api.py` both have `_sanitize()` that recursively replaces `float('nan')` / `float('inf')` with `None` before any `json.dumps` or API response. Applied in `_reports_to_dicts()`, `save_portfolio_results()`, and all `asdict()` call sites in `api.py`.
- **`math.floor(NaN)` crash in risk.py** — `compute_position_size()` guard changed from `risk_per_share <= 0` to `not (risk_per_share > 0)` so NaN is caught (NaN comparisons always return False in Python).
- **Stale running jobs** — `db.py` exposes `cleanup_stale_jobs()` which marks any `pending`/`running` jobs as `error` on server startup (called via `@app.on_event("startup")` in `api.py`). Prevents the frontend from picking up orphaned jobs whose worker process died silently.
- **`fmtScore` null crash** — `frontend/src/lib/utils.ts` `fmtScore()` now guards against `null`/`undefined` (returns `—`), since sanitized NaN fields arrive as `null` from the API.

## Recent algorithm improvements (implemented)
- **ADX-based dynamic weights** — trend/entry-timing weights shift based on ADX regime (trending vs ranging market)
- **Volume signal upgrade** — Chaikin Money Flow (CMF) + volume ratio replace OBV as primary volume signal; up/down volume ratio added as secondary
- **52-week high proximity** — George & Hwang anchoring effect: proximity to 52w high incorporated into trend score
- **SMA 50 macro trend** — price vs SMA50 added to trend factor as macro confirmation
- **Market regime filter (SPY)** — `fetch_spy_regime()` in `market_data.py` fetches SPY vs 200-day SMA; bear market raises Buy threshold by 5 pts, Watch by 3 pts. Fetched once per `run_engine()` call
- **Elder Impulse System** — EMA13 slope + MACD histogram slope; red bar = sell risk penalty in `hold_scoring.py`
- **RSI rollover** — RSI was ≥65 within last 10 bars, now <55 → momentum exhaustion penalty
- **MACD histogram fading** — histogram still positive but lost ≥60% of peak strength → early exit penalty

## Future work (not yet implemented)
- **Relative Strength (RS) Rating** — IBD-style percentile rank of a stock's 12-month price return vs the full universe. Requires maintaining a universe-wide return table and computing percentile ranks. High RS (≥ 80th percentile) should add to trend/momentum scores; low RS (< 20th) should penalise. Adds ~+5-8 pts of signal per Minervini's research.
- **Efficiency Ratio (ER)** — Perry Kaufman's noise-reduction metric: net price displacement / sum of absolute daily changes over N bars. ER near 1 = price moving efficiently in one direction (high conviction). ER near 0 = choppy/random. Could gate entry timing scores: only give full entry-timing credit when ER > 0.4. Reduces false positives in choppy markets.

## Environment
- Python 3.11 at `/Library/Frameworks/Python.framework/Versions/3.11/bin/python3`
- The Homebrew Python (`/opt/homebrew`) is a separate interpreter — IDE hints about missing packages can be ignored; packages are installed in the Framework Python
- `annie.db` is gitignored (generated at runtime)
