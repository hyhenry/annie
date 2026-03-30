"""
taapi_client.py — TAAPI.io API integration layer.

This module is responsible for ONE thing: fetching raw indicator data from the
TAAPI.io API (or returning mock data) and packaging it into a RawIndicatorBundle.

It knows nothing about scoring logic. It just fetches data and hands it off.

Three modes:
    1. Mock mode    — Returns pre-built sample data. No internet required.
    2. Free tier    — Calls each indicator endpoint individually (~10 calls per stock).
                      Rate-limited to comply with TAAPI's free plan restrictions.
    3. Bulk (Pro)   — One POST request fetches all indicators at once.
                      Requires a TAAPI Pro subscription.

Public functions:
    fetch_indicators(ticker, interval) → RawIndicatorBundle
    fetch_all_symbols()                → List[str]   (all US stocks on TAAPI)
"""

import json
import logging
import os
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RawIndicatorBundle:
    """
    Raw indicator values exactly as returned by TAAPI.io for one stock
    at one timeframe.

    All fields are Optional — if TAAPI fails to return a value, the field
    stays None and the scoring layer treats it as missing (with a small
    confidence penalty).
    """
    ticker:         str
    interval:       str

    # RSI: a number 0–100 measuring momentum (see config.py for explanation)
    rsi:            Optional[float] = None

    # MACD components: value = MACD line, signal = signal line,
    # histogram = difference between them (key momentum signal)
    macd_value:     Optional[float] = None
    macd_signal:    Optional[float] = None
    macd_histogram: Optional[float] = None

    # SMA/EMA: moving average lines used to identify trend direction
    sma_20:         Optional[float] = None
    ema_20:         Optional[float] = None

    # Bollinger Bands: upper/middle/lower price channel
    bb_upper:       Optional[float] = None
    bb_middle:      Optional[float] = None
    bb_lower:       Optional[float] = None

    # Stochastic oscillator: K and D lines (similar to RSI, 0–100)
    stoch_k:        Optional[float] = None
    stoch_d:        Optional[float] = None

    # ATR: average daily price range (used for stop-loss sizing)
    atr:            Optional[float] = None

    # ADX: trend strength 0–100 (does NOT tell direction, only strength)
    adx:            Optional[float] = None

    # OBV: cumulative volume indicator (positive = accumulation)
    obv:            Optional[float] = None

    # Current close price (from candle endpoint)
    close:          Optional[float] = None

    # Daily volume (from candle endpoint)
    volume:         Optional[float] = None

    # Any indicators that failed to load are recorded here for debugging
    fetch_errors:   List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# MOCK DATA
# ─────────────────────────────────────────────────────────────────────────────
# Five realistic stock scenarios, covering the full range of outcomes.
# These are used when MOCK_MODE=True — no API calls are made.

_MOCK_DATA: Dict[str, Dict[str, Dict[str, Any]]] = {

    # ── AAPL: Strong Buy ──────────────────────────────────────────────────────
    # All signals aligned: strong uptrend, healthy momentum, not overbought,
    # price in good entry zone, decent volume. Daily and 4h agree.
    "AAPL": {
        "1d": dict(
            rsi=58.4,
            macd_value=1.20, macd_signal=0.80, macd_histogram=0.40,
            sma_20=184.2, ema_20=185.1,
            bb_upper=191.0, bb_middle=185.0, bb_lower=179.0,
            stoch_k=62.0, stoch_d=58.0,
            atr=3.40, adx=28.5,
            obv=12_345_678,
            close=186.5, volume=820_000,
        ),
        "4h": dict(
            rsi=55.2,
            macd_value=0.65, macd_signal=0.48, macd_histogram=0.17,
            sma_20=185.8, ema_20=186.0,
            bb_upper=189.5, bb_middle=186.0, bb_lower=182.5,
            stoch_k=58.0, stoch_d=54.0,
            atr=1.80, adx=26.0,
            obv=3_200_000,
            close=186.5, volume=210_000,
        ),
    },

    # ── MSFT: Watch (score ~72) ───────────────────────────────────────────────
    # Uptrend intact but momentum cooling: RSI slightly above ideal zone,
    # MACD histogram thin, stochastic approaching overbought. Good stock,
    # but wait for a small pullback before buying.
    "MSFT": {
        "1d": dict(
            rsi=68.2,
            macd_value=3.10, macd_signal=2.80, macd_histogram=0.30,
            sma_20=410.5, ema_20=411.2,
            bb_upper=425.0, bb_middle=410.5, bb_lower=396.0,
            stoch_k=74.0, stoch_d=70.0,
            atr=6.10, adx=22.0,
            obv=5_520_000,
            close=415.0, volume=760_000,
        ),
        "4h": dict(
            rsi=71.0,
            macd_value=1.40, macd_signal=1.20, macd_histogram=0.20,
            sma_20=412.0, ema_20=412.5,
            bb_upper=420.0, bb_middle=412.0, bb_lower=404.0,
            stoch_k=78.0, stoch_d=75.0,
            atr=3.20, adx=20.0,
            obv=1_400_000,
            close=415.0, volume=190_000,
        ),
    },

    # ── NVDA: Watch (score ~64) ───────────────────────────────────────────────
    # Strong trend and momentum but very high ATR% (volatile stock).
    # RSI is overbought, stochastic also overbought. Large ATR means wide
    # stop-losses and bigger position-sizing risk. Score is Watch, not Buy.
    "NVDA": {
        "1d": dict(
            rsi=72.5,
            macd_value=8.50, macd_signal=6.20, macd_histogram=2.30,
            sma_20=850.0, ema_20=855.0,
            bb_upper=920.0, bb_middle=860.0, bb_lower=800.0,
            stoch_k=82.0, stoch_d=79.0,
            atr=28.5, adx=31.0,
            obv=8_900_000,
            close=875.0, volume=610_000,
        ),
        "4h": dict(
            rsi=69.0,
            macd_value=4.20, macd_signal=3.50, macd_histogram=0.70,
            sma_20=860.0, ema_20=862.0,
            bb_upper=890.0, bb_middle=865.0, bb_lower=840.0,
            stoch_k=76.0, stoch_d=74.0,
            atr=14.0, adx=29.0,
            obv=2_200_000,
            close=875.0, volume=155_000,
        ),
    },

    # ── AMC: Avoid (score ~42) ────────────────────────────────────────────────
    # Classic "avoid" setup: price below moving averages (downtrend),
    # MACD histogram negative (selling pressure), ADX weak (no real trend —
    # just drift). OBV low. No clear reason to buy.
    "AMC": {
        "1d": dict(
            rsi=44.0,
            macd_value=-0.18, macd_signal=-0.05, macd_histogram=-0.13,
            sma_20=9.20, ema_20=9.00,
            bb_upper=10.50, bb_middle=9.10, bb_lower=7.70,
            stoch_k=35.0, stoch_d=38.0,
            atr=0.62, adx=12.0,
            obv=-450_000,
            close=8.50, volume=1_250_000,
        ),
        "4h": dict(
            rsi=41.0,
            macd_value=-0.09, macd_signal=-0.04, macd_histogram=-0.05,
            sma_20=8.80, ema_20=8.70,
            bb_upper=9.60, bb_middle=8.80, bb_lower=8.00,
            stoch_k=32.0, stoch_d=35.0,
            atr=0.32, adx=10.0,
            obv=-120_000,
            close=8.50, volume=320_000,
        ),
    },

    # ── TSLA: Trim / Watch Closely (~score 58) ───────────────────────────────
    # Strong run followed by stalling momentum: RSI elevated, stochastic
    # overbought, MACD histogram shrinking. Position is profitable but
    # showing early signs of distribution. ATR is high.
    "TSLA": {
        "1d": dict(
            rsi=73.8,
            macd_value=5.20, macd_signal=5.00, macd_histogram=0.20,  # histogram shrinking
            sma_20=218.0, ema_20=220.0,
            bb_upper=255.0, bb_middle=225.0, bb_lower=195.0,
            stoch_k=84.0, stoch_d=87.0,  # K below D = rolling over
            atr=9.80, adx=24.0,
            obv=7_200_000,
            close=242.0, volume=680_000,
        ),
        "4h": dict(
            rsi=67.0,
            macd_value=2.10, macd_signal=2.30, macd_histogram=-0.20,  # 4h already negative
            sma_20=235.0, ema_20=236.0,
            bb_upper=250.0, bb_middle=237.0, bb_lower=224.0,
            stoch_k=72.0, stoch_d=78.0,
            atr=5.00, adx=21.0,
            obv=1_800_000,
            close=242.0, volume=170_000,
        ),
    },

    # ── SNDL: Auto-Avoid (hard filter — price below $5) ───────────────────────
    # This stock will be automatically rejected before scoring even starts
    # because its price is below the $5 minimum threshold. Regardless of
    # indicator values, penny stocks are excluded from recommendations.
    "SNDL": {
        "1d": dict(
            rsi=52.0,
            macd_value=0.02, macd_signal=0.01, macd_histogram=0.01,
            sma_20=1.90, ema_20=1.88,
            bb_upper=2.20, bb_middle=1.90, bb_lower=1.60,
            stoch_k=55.0, stoch_d=50.0,
            atr=0.08, adx=18.0,
            obv=2_100_000,
            close=1.85, volume=2_000_000,
        ),
        "4h": dict(
            rsi=50.0,
            macd_value=0.01, macd_signal=0.01, macd_histogram=0.00,
            sma_20=1.88, ema_20=1.87,
            bb_upper=1.95, bb_middle=1.88, bb_lower=1.81,
            stoch_k=52.0, stoch_d=50.0,
            atr=0.04, adx=16.0,
            obv=530_000,
            close=1.85, volume=500_000,
        ),
    },
}

# For any ticker not in the mock data dictionary, fall back to this neutral template.
_MOCK_FALLBACK: Dict[str, Any] = dict(
    rsi=50.0,
    macd_value=0.0, macd_signal=0.0, macd_histogram=0.0,
    sma_20=100.0, ema_20=100.0,
    bb_upper=105.0, bb_middle=100.0, bb_lower=95.0,
    stoch_k=50.0, stoch_d=50.0,
    atr=2.0, adx=20.0,
    obv=1_000_000,
    close=100.0, volume=600_000,
)


# ─────────────────────────────────────────────────────────────────────────────
# RATE LIMITING (thread-safe)
# ─────────────────────────────────────────────────────────────────────────────
# TAAPI's free tier allows roughly one request per second. This lock ensures
# that even if you were to run multiple tickers in threads, calls are spaced out.

_rate_lock = threading.Lock()
_last_call_time: float = 0.0


def _rate_limit() -> None:
    """Sleep if needed to respect TAAPI's free-tier rate limit."""
    global _last_call_time
    with _rate_lock:
        elapsed = time.monotonic() - _last_call_time
        wait = config.TAAPI_RATE_LIMIT_DELAY - elapsed
        if wait > 0:
            logger.debug("Rate limit: sleeping %.2fs", wait)
            time.sleep(wait)
        _last_call_time = time.monotonic()


# ─────────────────────────────────────────────────────────────────────────────
# HTTP SESSION
# ─────────────────────────────────────────────────────────────────────────────

def _build_session() -> requests.Session:
    """
    Create a requests Session with:
      • Automatic retry on server errors (5xx) and network timeouts
      • Connection keep-alive for efficiency
    """
    session = requests.Session()
    retry = Retry(
        total=config.TAAPI_MAX_RETRIES,
        backoff_factor=config.TAAPI_RETRY_BACKOFF_BASE,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    return session


# Shared session (created once, reused across all calls in a run)
_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = _build_session()
    return _session


# ─────────────────────────────────────────────────────────────────────────────
# INDIVIDUAL ENDPOINT CALLS
# ─────────────────────────────────────────────────────────────────────────────

def _taapi_symbol(ticker: str) -> str:
    """Convert 'AAPL' → 'AAPL/USD' (TAAPI's expected symbol format)."""
    return f"{ticker}{config.TAAPI_SYMBOL_SUFFIX}"


class TaapiPlanError(Exception):
    """
    Raised when TAAPI rejects a request because the API key's plan does not
    support US stocks.

    TAAPI free tier only covers crypto (Binance pairs). US stocks require at
    least a Basic paid plan. When this error is raised, the caller should
    stop making further API calls for the same ticker — they will all fail
    with the same 403 response.
    """
    pass


def _get_indicator(
    endpoint: str,
    ticker: str,
    interval: str,
    extra_params: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Call a single TAAPI indicator endpoint.

    Returns the parsed JSON response dict, or None if the call failed
    after all retries.

    Raises:
        TaapiPlanError — if the API key's plan does not allow US stocks.
                         The caller should abort further calls for this ticker.

    Args:
        endpoint:     TAAPI endpoint name, e.g. "rsi", "macd", "bbands"
        ticker:       Stock symbol, e.g. "AAPL"
        interval:     Timeframe, e.g. "1d", "4h"
        extra_params: Additional query parameters (e.g. {"period": 20})
    """
    _rate_limit()

    params: Dict[str, Any] = {
        "secret":   config.TAAPI_SECRET,
        "exchange": config.TAAPI_EXCHANGE,
        "symbol":   _taapi_symbol(ticker),
        "interval": interval,
    }
    if extra_params:
        params.update(extra_params)

    url = f"{config.TAAPI_BASE_URL}/{endpoint}"
    logger.debug("GET %s | ticker=%s interval=%s", endpoint, ticker, interval)

    try:
        resp = _get_session().get(url, params=params, timeout=config.TAAPI_TIMEOUT_SECONDS)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 403:
            # Check whether this is a plan restriction (not just an auth error)
            body = resp.text
            if "Free plans only permits" in body or "plan" in body.lower():
                raise TaapiPlanError(
                    "Your TAAPI plan does not support US stocks. "
                    "The free tier only covers crypto (BTC/USDT, ETH/USDT, etc.). "
                    "Upgrade to a Basic or higher plan at https://taapi.io/pricing/ "
                    "to scan US equities, or run with --mock / MOCK_MODE=true to use "
                    "built-in sample data without an API key."
                )
            logger.warning(
                "TAAPI %s returned HTTP 403 for %s/%s: %s",
                endpoint, ticker, interval, body[:200],
            )
            return None
        else:
            logger.warning(
                "TAAPI %s returned HTTP %d for %s/%s: %s",
                endpoint, resp.status_code, ticker, interval, resp.text[:200]
            )
            return None
    except TaapiPlanError:
        raise  # propagate — do not swallow
    except requests.exceptions.Timeout:
        logger.warning("TAAPI timeout: %s for %s/%s", endpoint, ticker, interval)
        return None
    except requests.exceptions.RequestException as exc:
        logger.warning("TAAPI request error: %s for %s/%s: %s", endpoint, ticker, interval, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# FREE-TIER FETCH (individual calls)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_free_tier(ticker: str, interval: str) -> RawIndicatorBundle:
    """
    Fetch all indicators using individual TAAPI GET endpoints.
    Suitable for free-tier accounts. Makes ~10 HTTP calls per stock per timeframe.

    If TAAPI rejects the first call with a plan-restriction 403, a TaapiPlanError
    is raised immediately so the caller can abort all remaining calls for this ticker.
    """
    bundle = RawIndicatorBundle(ticker=ticker, interval=interval)

    # ── RSI ──────────────────────────────────────────────────────────────────
    # TaapiPlanError propagates up — no need to catch here
    data = _get_indicator("rsi", ticker, interval)
    if data and "value" in data:
        bundle.rsi = float(data["value"])
    else:
        bundle.fetch_errors.append("rsi")

    # ── MACD ──────────────────────────────────────────────────────────────────
    data = _get_indicator("macd", ticker, interval)
    if data:
        bundle.macd_value     = _safe_float(data, "valueMACD")
        bundle.macd_signal    = _safe_float(data, "valueMACDSignal")
        bundle.macd_histogram = _safe_float(data, "valueMACDHist")
        if bundle.macd_value is None:
            bundle.fetch_errors.append("macd")
    else:
        bundle.fetch_errors.append("macd")

    # ── SMA (20-period) ────────────────────────────────────────────────────────
    data = _get_indicator("sma", ticker, interval, {"period": config.SMA_PERIOD})
    if data and "value" in data:
        bundle.sma_20 = float(data["value"])
    else:
        bundle.fetch_errors.append("sma")

    # ── EMA (20-period) ────────────────────────────────────────────────────────
    data = _get_indicator("ema", ticker, interval, {"period": config.EMA_PERIOD})
    if data and "value" in data:
        bundle.ema_20 = float(data["value"])
    else:
        bundle.fetch_errors.append("ema")

    # ── Bollinger Bands ────────────────────────────────────────────────────────
    data = _get_indicator("bbands", ticker, interval)
    if data:
        bundle.bb_upper  = _safe_float(data, "valueUpperBand")
        bundle.bb_middle = _safe_float(data, "valueMiddleBand")
        bundle.bb_lower  = _safe_float(data, "valueLowerBand")
        if bundle.bb_upper is None:
            bundle.fetch_errors.append("bbands")
    else:
        bundle.fetch_errors.append("bbands")

    # ── Stochastic ─────────────────────────────────────────────────────────────
    data = _get_indicator("stoch", ticker, interval)
    if data:
        # TAAPI uses "valueFastK" / "valueFastD" OR "valueK" / "valueD" depending on plan
        bundle.stoch_k = _safe_float(data, "valueFastK") or _safe_float(data, "valueK")
        bundle.stoch_d = _safe_float(data, "valueFastD") or _safe_float(data, "valueD")
        if bundle.stoch_k is None:
            bundle.fetch_errors.append("stoch")
    else:
        bundle.fetch_errors.append("stoch")

    # ── ATR ────────────────────────────────────────────────────────────────────
    data = _get_indicator("atr", ticker, interval)
    if data and "value" in data:
        bundle.atr = float(data["value"])
    else:
        bundle.fetch_errors.append("atr")

    # ── ADX ────────────────────────────────────────────────────────────────────
    data = _get_indicator("adx", ticker, interval)
    if data and "value" in data:
        bundle.adx = float(data["value"])
    else:
        bundle.fetch_errors.append("adx")

    # ── OBV ────────────────────────────────────────────────────────────────────
    data = _get_indicator("obv", ticker, interval)
    if data and "value" in data:
        bundle.obv = float(data["value"])
    else:
        bundle.fetch_errors.append("obv")

    # ── Candle (close price + volume) ──────────────────────────────────────────
    data = _get_indicator("candle", ticker, interval)
    if data:
        bundle.close  = _safe_float(data, "close")
        bundle.volume = _safe_float(data, "volume")
        if bundle.close is None:
            bundle.fetch_errors.append("candle")
    else:
        bundle.fetch_errors.append("candle")

    if bundle.fetch_errors:
        logger.info("Fetch errors for %s/%s: %s", ticker, interval, bundle.fetch_errors)

    return bundle


# ─────────────────────────────────────────────────────────────────────────────
# BULK ENDPOINT (Pro plan)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_bulk(ticker: str, interval: str) -> RawIndicatorBundle:
    """
    Fetch all indicators in a single POST /bulk request.
    Requires a TAAPI Pro subscription. Much faster than individual calls.
    """
    bundle = RawIndicatorBundle(ticker=ticker, interval=interval)

    payload = {
        "secret": config.TAAPI_SECRET,
        "construct": {
            "exchange": config.TAAPI_EXCHANGE,
            "symbol":   _taapi_symbol(ticker),
            "interval": interval,
            "indicators": [
                {"indicator": "rsi",    "id": "rsi"},
                {"indicator": "macd",   "id": "macd"},
                {"indicator": "sma",    "id": "sma",    "period": config.SMA_PERIOD},
                {"indicator": "ema",    "id": "ema",    "period": config.EMA_PERIOD},
                {"indicator": "bbands", "id": "bbands"},
                {"indicator": "stoch",  "id": "stoch"},
                {"indicator": "atr",    "id": "atr"},
                {"indicator": "adx",    "id": "adx"},
                {"indicator": "obv",    "id": "obv"},
                {"indicator": "candle", "id": "candle"},
            ],
        },
    }

    url = f"{config.TAAPI_BASE_URL}/bulk"
    logger.debug("POST /bulk | ticker=%s interval=%s", ticker, interval)

    try:
        resp = _get_session().post(
            url,
            json=payload,
            timeout=config.TAAPI_TIMEOUT_SECONDS * 2,
        )
        if resp.status_code != 200:
            logger.warning("Bulk endpoint returned HTTP %d for %s", resp.status_code, ticker)
            # Fall back to individual calls
            return _fetch_free_tier(ticker, interval)

        results = resp.json().get("data", [])
        result_map = {item["id"]: item.get("result", {}) for item in results}

        # Parse each indicator from the bulk response
        if "rsi" in result_map:
            bundle.rsi = _safe_float(result_map["rsi"], "value")
        if "macd" in result_map:
            d = result_map["macd"]
            bundle.macd_value     = _safe_float(d, "valueMACD")
            bundle.macd_signal    = _safe_float(d, "valueMACDSignal")
            bundle.macd_histogram = _safe_float(d, "valueMACDHist")
        if "sma" in result_map:
            bundle.sma_20 = _safe_float(result_map["sma"], "value")
        if "ema" in result_map:
            bundle.ema_20 = _safe_float(result_map["ema"], "value")
        if "bbands" in result_map:
            d = result_map["bbands"]
            bundle.bb_upper  = _safe_float(d, "valueUpperBand")
            bundle.bb_middle = _safe_float(d, "valueMiddleBand")
            bundle.bb_lower  = _safe_float(d, "valueLowerBand")
        if "stoch" in result_map:
            d = result_map["stoch"]
            bundle.stoch_k = _safe_float(d, "valueFastK") or _safe_float(d, "valueK")
            bundle.stoch_d = _safe_float(d, "valueFastD") or _safe_float(d, "valueD")
        if "atr" in result_map:
            bundle.atr = _safe_float(result_map["atr"], "value")
        if "adx" in result_map:
            bundle.adx = _safe_float(result_map["adx"], "value")
        if "obv" in result_map:
            bundle.obv = _safe_float(result_map["obv"], "value")
        if "candle" in result_map:
            bundle.close  = _safe_float(result_map["candle"], "close")
            bundle.volume = _safe_float(result_map["candle"], "volume")

    except requests.exceptions.RequestException as exc:
        logger.warning("Bulk fetch failed for %s: %s — falling back to individual calls", ticker, exc)
        return _fetch_free_tier(ticker, interval)

    return bundle


# ─────────────────────────────────────────────────────────────────────────────
# MOCK FETCH
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_mock(ticker: str, interval: str) -> RawIndicatorBundle:
    """
    Return pre-built sample data without making any API calls.
    Used when MOCK_MODE=True.
    """
    ticker_upper = ticker.upper()
    if ticker_upper in _MOCK_DATA and interval in _MOCK_DATA[ticker_upper]:
        raw = _MOCK_DATA[ticker_upper][interval]
    elif ticker_upper in _MOCK_DATA:
        # Use any available interval as fallback with slight variation
        any_interval = next(iter(_MOCK_DATA[ticker_upper]))
        raw = _MOCK_DATA[ticker_upper][any_interval]
        logger.debug("Mock: no interval %s for %s, using %s", interval, ticker, any_interval)
    else:
        logger.debug("Mock: no data for %s, using neutral fallback", ticker_upper)
        raw = _MOCK_FALLBACK

    return RawIndicatorBundle(
        ticker=ticker_upper,
        interval=interval,
        rsi=raw.get("rsi"),
        macd_value=raw.get("macd_value"),
        macd_signal=raw.get("macd_signal"),
        macd_histogram=raw.get("macd_histogram"),
        sma_20=raw.get("sma_20"),
        ema_20=raw.get("ema_20"),
        bb_upper=raw.get("bb_upper"),
        bb_middle=raw.get("bb_middle"),
        bb_lower=raw.get("bb_lower"),
        stoch_k=raw.get("stoch_k"),
        stoch_d=raw.get("stoch_d"),
        atr=raw.get("atr"),
        adx=raw.get("adx"),
        obv=raw.get("obv"),
        close=raw.get("close"),
        volume=raw.get("volume"),
        fetch_errors=[],
    )


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC INTERFACE
# ─────────────────────────────────────────────────────────────────────────────

def fetch_indicators(ticker: str, interval: str) -> RawIndicatorBundle:
    """
    Fetch all technical indicators for a stock at a given timeframe.

    This is the only function you need to call from outside this module.
    It automatically routes to mock / bulk / free-tier based on config settings.

    Args:
        ticker:   Stock symbol, e.g. "AAPL"
        interval: Timeframe code, e.g. "1d" (daily), "4h" (4-hour)

    Returns:
        RawIndicatorBundle with all available indicator values.
        Never raises — any failures are recorded in bundle.fetch_errors.
        If the API key lacks permission for US stocks, fetch_errors will contain
        "plan_restriction" and the error message will explain the upgrade path.
    """
    ticker = ticker.upper().strip()

    if config.MOCK_MODE:
        logger.debug("Mock mode: returning sample data for %s/%s", ticker, interval)
        return _fetch_mock(ticker, interval)

    if not config.TAAPI_SECRET:
        logger.error(
            "TAAPI_SECRET is not set. Either set it in .env or run with --mock. "
            "Returning empty bundle for %s.", ticker
        )
        return RawIndicatorBundle(
            ticker=ticker, interval=interval,
            fetch_errors=["no_api_key"]
        )

    try:
        if config.USE_BULK_API:
            logger.debug("Bulk mode: fetching all indicators for %s/%s", ticker, interval)
            return _fetch_bulk(ticker, interval)

        logger.debug("Free-tier mode: fetching indicators one by one for %s/%s", ticker, interval)
        return _fetch_free_tier(ticker, interval)

    except TaapiPlanError as exc:
        logger.error(
            "Plan restriction for %s/%s — stopping all calls for this ticker. %s",
            ticker, interval, exc,
        )
        return RawIndicatorBundle(
            ticker=ticker,
            interval=interval,
            fetch_errors=["plan_restriction"],
        )


# ─────────────────────────────────────────────────────────────────────────────
# SYMBOL LIST
# ─────────────────────────────────────────────────────────────────────────────

# In-memory cache — populated on first call to fetch_all_symbols()
_symbols_cache: Optional[List[str]] = None


def fetch_all_symbols() -> List[str]:
    """
    Return the complete list of US stock symbols available on TAAPI.

    In mock mode: reads the bundled all_symbols.json file (no internet needed).
    In live mode: calls GET /exchange-symbols once and caches the result for the
                  rest of the process lifetime.

    Returns:
        List of ticker strings, e.g. ["AAPL", "MSFT", "NVDA", ...]

    Never raises — falls back to the bundled list on any network error.
    """
    global _symbols_cache
    if _symbols_cache is not None:
        return _symbols_cache

    if config.MOCK_MODE:
        _symbols_cache = _load_bundled_symbols()
        logger.info("Mock mode: loaded %d symbols from all_symbols.json", len(_symbols_cache))
        return _symbols_cache

    if not config.TAAPI_SECRET:
        logger.warning("No TAAPI_SECRET — cannot fetch symbol list; using bundled list.")
        _symbols_cache = _load_bundled_symbols()
        return _symbols_cache

    _rate_limit()
    url    = f"{config.TAAPI_BASE_URL}/exchange-symbols"
    params = {"secret": config.TAAPI_SECRET, "type": "stocks"}
    logger.info("Fetching symbol list from TAAPI …")

    try:
        resp = _get_session().get(url, params=params, timeout=config.TAAPI_TIMEOUT_SECONDS)
        if resp.status_code == 200:
            data = resp.json()
            # API returns either a plain list or {"data": [...]}
            raw = data if isinstance(data, list) else data.get("data", [])
            _symbols_cache = sorted(str(s).upper().strip() for s in raw if s)
            logger.info("Fetched %d symbols from TAAPI exchange-symbols endpoint", len(_symbols_cache))
            return _symbols_cache
        else:
            logger.warning(
                "exchange-symbols returned HTTP %d — falling back to bundled list",
                resp.status_code,
            )
    except requests.exceptions.RequestException as exc:
        logger.warning("Failed to fetch symbol list: %s — using bundled list", exc)

    _symbols_cache = _load_bundled_symbols()
    return _symbols_cache


def _load_bundled_symbols() -> List[str]:
    """
    Load the bundled all_symbols.json file that ships with the project.
    Falls back to tickers.json, then the five mock tickers, if not found.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for fname in ("all_symbols.json", "tickers.json"):
        path = os.path.join(here, fname)
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, list):
                return [str(s).upper() for s in data if s]
        except Exception:
            pass
    # Ultimate fallback: just the five built-in mock tickers
    return list(_MOCK_DATA.keys())


def clear_symbols_cache() -> None:
    """Force the next call to fetch_all_symbols() to re-fetch from TAAPI."""
    global _symbols_cache
    _symbols_cache = None


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _safe_float(data: Dict[str, Any], key: str) -> Optional[float]:
    """
    Safely extract a float from a dict.
    Returns None if the key is missing or the value cannot be converted.
    """
    val = data.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
