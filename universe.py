"""
universe.py — Stock universe builder.

Downloads S&P 500 and NASDAQ 100 constituents from Wikipedia, enriches each
ticker with sector and industry metadata, and caches the result in SQLite so
it loads instantly after the first fetch.

The cached universe is refreshed automatically once every 7 days, or on demand.

Public API:
    get_universe()                  → pd.DataFrame  (ticker, name, sector, industry, source)
    get_sectors()                   → List[str]      sorted unique sector names
    get_tickers_for_sectors(...)    → List[str]      tickers matching selected sectors
    refresh_universe()              → pd.DataFrame   force re-download and re-cache
"""

import logging
import sqlite3
import os
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Where the cache lives (same DB as scan results)
_DB_PATH: str = os.getenv("ANNIE_DB_PATH", "annie.db")
_CACHE_TTL_DAYS: int = 7

# Wikipedia tables for index constituents
_SP500_URL   = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_NDX100_URL  = "https://en.wikipedia.org/wiki/Nasdaq-100"

# Fallback minimal universe in case Wikipedia is unreachable
_FALLBACK_TICKERS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "JPM",
    "V", "AMD", "UNH", "XOM", "JNJ", "WMT", "MA", "PG", "HD", "AVGO",
    "MRK", "CVX", "PEP", "COST", "ABBV", "KO", "LLY", "MCD", "BAC",
    "TMO", "CSCO", "ACN", "ABT", "DHR", "NKE", "ADBE", "TXN", "NEE",
    "QCOM", "CRM", "INTC", "INTU", "PM", "IBM", "RTX", "CAT", "GS",
    "BLK", "SPGI", "AXP", "ISRG", "DE", "MDT", "SYK", "VRTX", "GILD",
    "AMT", "PLD", "CI", "BDX", "REGN", "ZTS", "C", "SCHW", "CB",
    "AON", "TJX", "USB", "ETN", "LMT", "MO", "DUK", "SO", "CL",
    "MMC", "BSX", "ICE", "ITW", "NOC", "HCA", "F", "GM", "PGR",
    "HUM", "MCK", "ADP", "BIIB", "MRNA", "AMGN", "DXCM", "IDXX",
    "PANW", "CRWD", "SNOW", "PLTR", "NET", "DDOG", "ZS", "OKTA",
    "COIN", "MARA", "RIOT", "SOFI", "HOOD", "RIVN", "LCID",
]


# ─────────────────────────────────────────────────────────────────────────────
# DB HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _ensure_table(c: sqlite3.Connection) -> None:
    c.execute("""
        CREATE TABLE IF NOT EXISTS universe (
            ticker      TEXT PRIMARY KEY,
            name        TEXT,
            sector      TEXT,
            industry    TEXT,
            source      TEXT,
            fetched_at  TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS universe_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    c.commit()


def _cache_age_days() -> Optional[float]:
    """Return how many days since the universe was last fetched, or None if never."""
    try:
        if not os.path.exists(_DB_PATH):
            return None
        c = _conn()
        _ensure_table(c)
        row = c.execute(
            "SELECT value FROM universe_meta WHERE key='fetched_at'"
        ).fetchone()
        c.close()
        if row is None:
            return None
        ts = datetime.fromisoformat(row["value"])
        return (datetime.now() - ts).total_seconds() / 86400
    except Exception:
        return None


def _write_cache(df: pd.DataFrame) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    c = _conn()
    _ensure_table(c)
    c.execute("DELETE FROM universe")
    c.executemany(
        "INSERT INTO universe (ticker, name, sector, industry, source, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (row.ticker, row.get("name", ""), row.get("sector", "Unknown"),
             row.get("industry", "Unknown"), row.get("source", ""), now)
            for row in df.itertuples(index=False)
        ],
    )
    c.execute(
        "INSERT OR REPLACE INTO universe_meta (key, value) VALUES ('fetched_at', ?)",
        (now,),
    )
    c.commit()
    c.close()
    logger.info("Universe cached: %d tickers", len(df))


def _read_cache() -> Optional[pd.DataFrame]:
    try:
        if not os.path.exists(_DB_PATH):
            return None
        c = _conn()
        _ensure_table(c)
        rows = c.execute(
            "SELECT ticker, name, sector, industry, source FROM universe ORDER BY ticker"
        ).fetchall()
        c.close()
        if not rows:
            return None
        return pd.DataFrame([dict(r) for r in rows])
    except Exception as exc:
        logger.warning("Failed to read universe cache: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# WIKIPEDIA FETCHERS
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_sp500() -> pd.DataFrame:
    """
    Download S&P 500 constituents from Wikipedia.
    Returns DataFrame with columns: ticker, name, sector, industry, source.
    """
    logger.info("Fetching S&P 500 from Wikipedia…")
    tables = pd.read_html(_SP500_URL, attrs={"id": "constituents"})
    df = tables[0]

    # Column names vary slightly — normalise
    col_map = {}
    for col in df.columns:
        cl = col.lower()
        if "symbol" in cl or "ticker" in cl:
            col_map[col] = "ticker"
        elif "security" in cl or "company" in cl or "name" in cl:
            col_map[col] = "name"
        elif "gics sector" in cl or ("sector" in cl and "sub" not in cl):
            col_map[col] = "sector"
        elif "gics sub" in cl or "sub-industry" in cl or "industry" in cl:
            col_map[col] = "industry"
    df = df.rename(columns=col_map)[["ticker", "name", "sector", "industry"]]

    # BRK.B → BRK-B (yfinance format)
    df["ticker"] = df["ticker"].str.replace(".", "-", regex=False).str.strip()
    df["source"] = "S&P 500"
    logger.info("S&P 500: %d tickers", len(df))
    return df


def _fetch_ndx100() -> pd.DataFrame:
    """
    Download NASDAQ 100 constituents from Wikipedia.
    Returns DataFrame with same columns as _fetch_sp500.
    """
    logger.info("Fetching NASDAQ 100 from Wikipedia…")
    tables = pd.read_html(_NDX100_URL)

    # Find the table that has both a ticker-like column and a company name
    target = None
    for t in tables:
        cols = [c.lower() for c in t.columns]
        if any("ticker" in c or "symbol" in c for c in cols):
            target = t
            break
        # Sometimes column is just unnamed — look for a column that looks like tickers
        for col in t.columns:
            sample = t[col].dropna().head(5).astype(str).tolist()
            if all(s.isupper() and 1 <= len(s) <= 5 for s in sample):
                target = t
                break
        if target is not None:
            break

    if target is None:
        logger.warning("Could not find NASDAQ 100 table on Wikipedia")
        return pd.DataFrame(columns=["ticker", "name", "sector", "industry", "source"])

    col_map = {}
    for col in target.columns:
        cl = str(col).lower()
        if "ticker" in cl or "symbol" in cl:
            col_map[col] = "ticker"
        elif "company" in cl or "security" in cl or "name" in cl:
            col_map[col] = "name"
        elif "sector" in cl and "sub" not in cl:
            col_map[col] = "sector"
        elif "industry" in cl or "sub" in cl:
            col_map[col] = "industry"

    target = target.rename(columns=col_map)
    for needed in ["ticker", "name", "sector", "industry"]:
        if needed not in target.columns:
            target[needed] = ""

    df = target[["ticker", "name", "sector", "industry"]].copy()
    df["ticker"] = df["ticker"].astype(str).str.strip()
    df["source"] = "NASDAQ 100"
    df = df[df["ticker"].str.match(r"^[A-Z]{1,5}(-[A-Z])?$", na=False)]
    logger.info("NASDAQ 100: %d tickers", len(df))
    return df


def _fetch_from_wikipedia() -> pd.DataFrame:
    """Combine S&P 500 + NASDAQ 100, deduplicate, return unified DataFrame."""
    frames = []
    for fetcher in (_fetch_sp500, _fetch_ndx100):
        try:
            frames.append(fetcher())
        except Exception as exc:
            logger.warning("Fetch failed: %s", exc)

    if not frames:
        logger.error("All Wikipedia fetches failed — using fallback ticker list")
        return _fallback_df()

    combined = pd.concat(frames, ignore_index=True)
    # Keep S&P 500 entry when a ticker appears in both (it has richer sector data)
    combined = combined.drop_duplicates(subset="ticker", keep="first")
    combined = combined[combined["ticker"].str.strip() != ""]
    combined = combined.reset_index(drop=True)

    # Fill any blank sector/industry with Unknown
    combined["sector"]   = combined["sector"].fillna("Unknown").replace("", "Unknown")
    combined["industry"] = combined["industry"].fillna("Unknown").replace("", "Unknown")
    return combined


def _fallback_df() -> pd.DataFrame:
    rows = [{"ticker": t, "name": "", "sector": "Unknown",
             "industry": "Unknown", "source": "fallback"}
            for t in _FALLBACK_TICKERS]
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def get_universe(force_refresh: bool = False) -> pd.DataFrame:
    """
    Return the full stock universe as a DataFrame.

    Columns: ticker, name, sector, industry, source

    Loads from SQLite cache if fresh (< 7 days old). Otherwise re-fetches
    from Wikipedia and updates the cache. Falls back to a hardcoded list
    if Wikipedia is unreachable.

    Args:
        force_refresh: Ignore cache age and re-download now.
    """
    age = _cache_age_days()
    if not force_refresh and age is not None and age < _CACHE_TTL_DAYS:
        cached = _read_cache()
        if cached is not None and len(cached) > 0:
            logger.debug("Universe loaded from cache (%.1f days old, %d tickers)", age, len(cached))
            return cached

    logger.info("Universe cache missing or stale (age=%s days) — refreshing", age)
    df = _fetch_from_wikipedia()
    _write_cache(df)
    return df


def refresh_universe() -> pd.DataFrame:
    """Force a re-download of the universe from Wikipedia."""
    return get_universe(force_refresh=True)


def get_sectors(universe: Optional[pd.DataFrame] = None) -> List[str]:
    """Return sorted list of unique sector names in the universe."""
    if universe is None:
        universe = get_universe()
    sectors = sorted(universe["sector"].dropna().unique().tolist())
    return [s for s in sectors if s and s != "Unknown"]


def get_tickers_for_sectors(
    sectors: List[str],
    universe: Optional[pd.DataFrame] = None,
) -> List[str]:
    """
    Return sorted ticker list for the given sector(s).

    Pass an empty list to get all tickers in the universe.
    """
    if universe is None:
        universe = get_universe()
    if not sectors:
        return sorted(universe["ticker"].tolist())
    mask = universe["sector"].isin(sectors)
    return sorted(universe.loc[mask, "ticker"].tolist())


def get_ticker_info(ticker: str, universe: Optional[pd.DataFrame] = None) -> dict:
    """Return the universe row for a ticker as a dict, or empty dict if not found."""
    if universe is None:
        universe = get_universe()
    rows = universe[universe["ticker"] == ticker.upper()]
    if rows.empty:
        return {}
    return rows.iloc[0].to_dict()
