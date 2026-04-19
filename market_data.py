"""
market_data.py — Market data fetcher (yfinance backend).

Fetches raw technical indicator data for a stock at a given timeframe using
Yahoo Finance (yfinance) and computes indicators locally with the `ta` library.

Two modes:
    1. Mock mode  — Returns pre-built sample data. No internet required.
    2. Live mode  — Downloads OHLCV from Yahoo Finance and computes indicators.

Public functions:
    fetch_indicators(ticker, interval) → RawIndicatorBundle
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RawIndicatorBundle:
    """
    Raw indicator values for one stock at one timeframe.

    All fields are Optional — if a value cannot be computed, the field stays
    None and the scoring layer treats it as missing (with a small confidence
    penalty).
    """
    ticker:         str
    interval:       str

    rsi:            Optional[float] = None

    macd_value:     Optional[float] = None
    macd_signal:    Optional[float] = None
    macd_histogram: Optional[float] = None

    sma_20:         Optional[float] = None
    ema_20:         Optional[float] = None

    bb_upper:       Optional[float] = None
    bb_middle:      Optional[float] = None
    bb_lower:       Optional[float] = None

    stoch_k:        Optional[float] = None
    stoch_d:        Optional[float] = None

    atr:            Optional[float] = None
    adx:            Optional[float] = None
    obv:            Optional[float] = None

    close:          Optional[float] = None
    volume:         Optional[float] = None
    volume_sma_20:  Optional[float] = None   # 20-period SMA of volume (for volume_ratio)

    # ── Enhancement fields (populated in live mode; None in mock/fallback) ─
    sma_50:              Optional[float] = None   # 50-period SMA
    high_52w:            Optional[float] = None   # 252-bar rolling max of highs (52-week high)
    cmf:                 Optional[float] = None   # 20-period Chaikin Money Flow (-1 to 1)
    up_down_vol_ratio:   Optional[float] = None   # up-day vol / total vol over 10 bars (0-1)
    ema_13:              Optional[float] = None   # 13-period EMA (Elder Impulse System)
    ema_13_prev:         Optional[float] = None   # EMA 13 from 3 bars ago
    macd_histogram_prev: Optional[float] = None   # MACD histogram 3 bars ago (fading/Elder)
    rsi_10_high:         Optional[float] = None   # max RSI over prior 10 bars (rollover detect)

    fetch_errors:   List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# MOCK DATA
# ─────────────────────────────────────────────────────────────────────────────
# Five realistic stock scenarios covering the full range of outcomes.
# Used when MOCK_MODE=True — no network calls are made.

_MOCK_DATA: Dict[str, Dict[str, Dict[str, Any]]] = {

    # ── AAPL: Strong Buy ──────────────────────────────────────────────────────
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

    # ── TSLA: Watch (score ~58) ───────────────────────────────────────────────
    "TSLA": {
        "1d": dict(
            rsi=73.8,
            macd_value=5.20, macd_signal=5.00, macd_histogram=0.20,
            sma_20=218.0, ema_20=220.0,
            bb_upper=255.0, bb_middle=225.0, bb_lower=195.0,
            stoch_k=84.0, stoch_d=87.0,
            atr=9.80, adx=24.0,
            obv=7_200_000,
            close=242.0, volume=680_000,
        ),
        "4h": dict(
            rsi=67.0,
            macd_value=2.10, macd_signal=2.30, macd_histogram=-0.20,
            sma_20=235.0, ema_20=236.0,
            bb_upper=250.0, bb_middle=237.0, bb_lower=224.0,
            stoch_k=72.0, stoch_d=78.0,
            atr=5.00, adx=21.0,
            obv=1_800_000,
            close=242.0, volume=170_000,
        ),
    },

    # ── SNDL: Auto-Avoid (hard filter — price below $5) ───────────────────────
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

# Neutral fallback for any ticker not in the mock dictionary
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
# MOCK FETCH
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_mock(ticker: str, interval: str) -> RawIndicatorBundle:
    """Return pre-built sample data without making any network calls."""
    ticker_upper = ticker.upper()
    if ticker_upper in _MOCK_DATA and interval in _MOCK_DATA[ticker_upper]:
        raw = _MOCK_DATA[ticker_upper][interval]
    elif ticker_upper in _MOCK_DATA:
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
# YFINANCE BACKEND
# ─────────────────────────────────────────────────────────────────────────────

try:
    import yfinance as _yf
    import ta as _ta
    _YFINANCE_AVAILABLE = True
except ImportError:
    _YFINANCE_AVAILABLE = False

# Map interval codes → (yfinance period, yfinance interval, resample rule)
# yfinance has no native 4h stock bars; we download 1h and resample.
_YF_INTERVAL_MAP: Dict[str, tuple] = {
    "1d":  ("3mo",  "1d",  None),
    "4h":  ("60d",  "1h",  "4h"),
    "1h":  ("7d",   "1h",  None),
    "1w":  ("1y",   "1wk", None),
    "15m": ("5d",   "15m", None),
}


def _last(series) -> Optional[float]:
    """Return the last non-NaN value from a pandas Series, or None."""
    try:
        if series is None or series.empty:
            return None
        val = series.iloc[-1]
        return None if (val != val or math.isnan(val)) else float(val)
    except Exception:
        return None


def _fetch_yfinance(ticker: str, interval: str) -> RawIndicatorBundle:
    """
    Download OHLCV data from Yahoo Finance and compute all indicators locally
    using the `ta` library. Returns a fully populated RawIndicatorBundle.

    Args:
        ticker:   Stock symbol, e.g. "AAPL"
        interval: Timeframe code, e.g. "1d" or "4h"
    """
    bundle = RawIndicatorBundle(ticker=ticker, interval=interval)

    if not _YFINANCE_AVAILABLE:
        logger.error("yfinance/ta not installed. Run: pip install yfinance ta")
        bundle.fetch_errors.append("yfinance_not_installed")
        return bundle

    yf_period, yf_interval, resample_rule = _YF_INTERVAL_MAP.get(
        interval, ("3mo", "1d", None)
    )

    try:
        hist = _yf.Ticker(ticker).history(
            period=yf_period,
            interval=yf_interval,
            auto_adjust=True,
        )

        if hist is None or hist.empty:
            logger.warning("yfinance: no data returned for %s/%s", ticker, interval)
            bundle.fetch_errors.append("no_data")
            return bundle

        hist.columns = [c.lower() for c in hist.columns]

        if resample_rule:
            hist = hist.resample(resample_rule).agg({
                "open":   "first",
                "high":   "max",
                "low":    "min",
                "close":  "last",
                "volume": "sum",
            }).dropna()

        if len(hist) < 30:
            logger.warning(
                "yfinance: only %d bars for %s/%s — need ≥30 for reliable indicators",
                len(hist), ticker, interval,
            )

        close  = hist["close"]
        high   = hist["high"]
        low    = hist["low"]
        volume = hist["volume"]

        bundle.close  = float(close.iloc[-1])
        bundle.volume = float(volume.iloc[-1])

        # ── RSI — save series for historical lookback ─────────────────────
        rsi_series = _ta.momentum.RSIIndicator(close, window=14).rsi()
        bundle.rsi = _last(rsi_series)
        if bundle.rsi is None:
            bundle.fetch_errors.append("rsi")
        rsi_clean = rsi_series.dropna()
        if len(rsi_clean) >= 11:
            bundle.rsi_10_high = float(rsi_clean.iloc[-11:-1].max())

        # ── MACD — save series for histogram history ───────────────────────
        macd_ind = _ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
        macd_diff_series = macd_ind.macd_diff()
        bundle.macd_value     = _last(macd_ind.macd())
        bundle.macd_signal    = _last(macd_ind.macd_signal())
        bundle.macd_histogram = _last(macd_diff_series)
        if bundle.macd_value is None:
            bundle.fetch_errors.append("macd")
        diff_clean = macd_diff_series.dropna()
        if len(diff_clean) >= 4:
            bundle.macd_histogram_prev = float(diff_clean.iloc[-4])

        # ── Moving averages ───────────────────────────────────────────────
        bundle.sma_20 = _last(_ta.trend.SMAIndicator(close, window=20).sma_indicator())
        bundle.ema_20 = _last(_ta.trend.EMAIndicator(close, window=20).ema_indicator())
        bundle.sma_50 = _last(_ta.trend.SMAIndicator(close, window=50).sma_indicator())

        # ── EMA 13 — Elder Impulse System ─────────────────────────────────
        ema13_series = _ta.trend.EMAIndicator(close, window=13).ema_indicator()
        bundle.ema_13 = _last(ema13_series)
        ema13_clean = ema13_series.dropna()
        if len(ema13_clean) >= 4:
            bundle.ema_13_prev = float(ema13_clean.iloc[-4])

        # ── Bollinger Bands ───────────────────────────────────────────────
        bb = _ta.volatility.BollingerBands(close, window=20, window_dev=2)
        bundle.bb_upper  = _last(bb.bollinger_hband())
        bundle.bb_middle = _last(bb.bollinger_mavg())
        bundle.bb_lower  = _last(bb.bollinger_lband())

        # ── Stochastic ────────────────────────────────────────────────────
        stoch = _ta.momentum.StochasticOscillator(high, low, close, window=14, smooth_window=3)
        bundle.stoch_k = _last(stoch.stoch())
        bundle.stoch_d = _last(stoch.stoch_signal())

        # ── ATR / ADX ─────────────────────────────────────────────────────
        bundle.atr = _last(_ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range())
        if bundle.atr is None:
            bundle.fetch_errors.append("atr")

        bundle.adx = _last(_ta.trend.ADXIndicator(high, low, close, window=14).adx())
        if bundle.adx is None:
            bundle.fetch_errors.append("adx")

        # ── Volume indicators ──────────────────────────────────────────────
        bundle.obv = _last(_ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume())
        bundle.volume_sma_20 = _last(_ta.trend.SMAIndicator(volume.astype(float), window=20).sma_indicator())
        bundle.cmf = _last(_ta.volume.ChaikinMoneyFlowIndicator(high, low, close, volume, window=20).chaikin_money_flow())

        # Up/down volume ratio over last 10 bars
        if len(hist) >= 11:
            last_10 = hist.iloc[-10:]
            prev_close = hist["close"].iloc[-11:-1]
            up_mask = last_10["close"].values > prev_close.values
            total_vol = float(last_10["volume"].sum())
            if total_vol > 0:
                up_vol = float(last_10["volume"].values[up_mask].sum())
                bundle.up_down_vol_ratio = up_vol / total_vol

        # ── 52-week high ─────────────────────────────────────────────────
        lookback = min(252, len(hist))
        if lookback >= 20:
            bundle.high_52w = float(high.iloc[-lookback:].max())

        logger.debug(
            "yfinance %s/%s: close=%.2f rsi=%.1f atr=%.2f adx=%.1f bars=%d",
            ticker, interval,
            bundle.close or 0, bundle.rsi or 0,
            bundle.atr or 0, bundle.adx or 0,
            len(hist),
        )

    except Exception as exc:
        logger.warning("yfinance fetch error for %s/%s: %s", ticker, interval, exc)
        bundle.fetch_errors.append("yfinance_error")

    return bundle


# ─────────────────────────────────────────────────────────────────────────────
# MARKET REGIME
# ─────────────────────────────────────────────────────────────────────────────

_SPY_REGIME_CACHE: Optional[str] = None


def fetch_spy_regime() -> str:
    """
    Return the current broad-market regime based on SPY vs its 200-day SMA.

    Returns 'bull', 'bear', or 'unknown'. Result is cached for the process
    lifetime so that all tickers in one scan run share the same reading
    without repeated network calls.

    In mock mode always returns 'bull' (no network call).

    Why it matters: Buy signals generated during a broad market downtrend
    have significantly lower hit rates. Raising the Buy threshold in a bear
    market reduces false positives without changing the score itself.
    """
    global _SPY_REGIME_CACHE
    if _SPY_REGIME_CACHE is not None:
        return _SPY_REGIME_CACHE

    if config.MOCK_MODE:
        _SPY_REGIME_CACHE = "bull"
        return _SPY_REGIME_CACHE

    if not _YFINANCE_AVAILABLE:
        _SPY_REGIME_CACHE = "unknown"
        return _SPY_REGIME_CACHE

    try:
        hist = _yf.Ticker("SPY").history(period="1y", interval="1d", auto_adjust=True)
        if hist is None or hist.empty:
            _SPY_REGIME_CACHE = "unknown"
            return _SPY_REGIME_CACHE

        hist.columns = [c.lower() for c in hist.columns]
        close = hist["close"]
        sma_200 = close.rolling(200).mean().iloc[-1]
        spy_close = float(close.iloc[-1])

        _SPY_REGIME_CACHE = "bull" if spy_close > float(sma_200) else "bear"
        logger.info(
            "Market regime: %s (SPY=%.2f, SMA200=%.2f)",
            _SPY_REGIME_CACHE, spy_close, float(sma_200),
        )
    except Exception as exc:
        logger.warning("Could not determine market regime: %s", exc)
        _SPY_REGIME_CACHE = "unknown"

    return _SPY_REGIME_CACHE


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC INTERFACE
# ─────────────────────────────────────────────────────────────────────────────

def fetch_indicators(ticker: str, interval: str) -> RawIndicatorBundle:
    """
    Fetch all technical indicators for a stock at a given timeframe.

    Routes to mock data or Yahoo Finance depending on MOCK_MODE.

    Args:
        ticker:   Stock symbol, e.g. "AAPL"
        interval: Timeframe code, e.g. "1d" (daily), "4h" (4-hour)

    Returns:
        RawIndicatorBundle with all available indicator values.
        Never raises — any failures are recorded in bundle.fetch_errors.
    """
    ticker = ticker.upper().strip()

    if config.MOCK_MODE:
        logger.debug("Mock mode: returning sample data for %s/%s", ticker, interval)
        return _fetch_mock(ticker, interval)

    logger.debug("yfinance: fetching %s/%s", ticker, interval)
    return _fetch_yfinance(ticker, interval)
