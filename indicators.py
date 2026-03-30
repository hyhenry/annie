"""
indicators.py — Parse raw TAAPI data and normalize each indicator to 0-100.

This module does two things:
    1. Converts a RawIndicatorBundle into a clean IndicatorData dataclass
       (computes derived fields like ATR%, Bollinger Band position, etc.)
    2. Provides one normalization function per indicator, each returning a
       sub-score from 0 to 100.

WHY NORMALIZE TO 0-100?
    RSI lives on a 0-100 scale. ATR lives in dollars. ADX lives 0-100 but
    has different meaning. To combine them fairly in a weighted average, we
    first convert each to the same 0-100 "quality score" scale.

    Think of it like converting temperatures: before you can average Celsius
    and Fahrenheit readings, you must convert them to the same unit.

All normalization functions:
    • Accept Optional[float] as input
    • Return float (0-100)
    • Return 50.0 (neutral) when input is None (missing data = no opinion)
"""

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional

from taapi_client import RawIndicatorBundle
import config

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR DATA DATACLASS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class IndicatorData:
    """
    Cleaned and enriched indicator data for one stock at one timeframe.

    Contains both the raw values (for display in output JSON) and derived
    fields computed from those values (used by the scoring engine).
    """
    ticker:   str
    interval: str

    # ── Raw indicator values (exactly as returned by TAAPI) ────────────────
    rsi:        Optional[float] = None
    macd:       Optional[dict]  = None   # {value, signal, histogram}
    adx:        Optional[float] = None
    atr:        Optional[float] = None
    sma_20:     Optional[float] = None
    ema_20:     Optional[float] = None
    bbands:     Optional[dict]  = None   # {upper, middle, lower}
    stoch:      Optional[dict]  = None   # {k, d}
    obv:        Optional[float] = None
    close:      Optional[float] = None
    volume:     Optional[float] = None

    # ── Derived fields (computed in parse_indicators) ──────────────────────
    # ATR as a percentage of the stock price (e.g. ATR=3.4 on a $186 stock = 1.83%)
    # Higher % means higher risk per trade.
    atr_pct: Optional[float] = None

    # Is the current price above the 20-day Simple Moving Average?
    # True = uptrend signal. False = downtrend warning.
    price_above_sma: Optional[bool] = None

    # Is the current price above the 20-day Exponential Moving Average?
    price_above_ema: Optional[bool] = None

    # Where does the price sit within the Bollinger Bands?
    # 0.0 = at lower band, 0.5 = at middle, 1.0 = at upper band, >1.0 = extended
    bb_position: Optional[float] = None

    # Fraction of critical indicators that returned data (0.0 to 1.0)
    # Used to compute the confidence score.
    data_completeness: float = 0.0

    # List of indicators that returned None (for logging and output)
    missing_indicators: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# PARSER: RawIndicatorBundle → IndicatorData
# ─────────────────────────────────────────────────────────────────────────────

def parse_indicators(raw: RawIndicatorBundle) -> IndicatorData:
    """
    Convert a RawIndicatorBundle into a clean IndicatorData object.

    Computes all derived fields (ATR%, BB position, MA comparisons) and
    calculates data_completeness.

    Args:
        raw: The raw bundle returned by taapi_client.fetch_indicators()

    Returns:
        IndicatorData ready for use by the scoring engine.
    """
    data = IndicatorData(ticker=raw.ticker, interval=raw.interval)

    # ── Copy raw values ────────────────────────────────────────────────────
    data.rsi    = raw.rsi
    data.adx    = raw.adx
    data.atr    = raw.atr
    data.sma_20 = raw.sma_20
    data.ema_20 = raw.ema_20
    data.obv    = raw.obv
    data.close  = raw.close
    data.volume = raw.volume

    # MACD packaged as a dict for clean JSON output
    if raw.macd_value is not None:
        data.macd = {
            "value":     raw.macd_value,
            "signal":    raw.macd_signal,
            "histogram": raw.macd_histogram,
        }
    else:
        data.macd = None

    # Bollinger Bands packaged as a dict
    if raw.bb_upper is not None:
        data.bbands = {
            "upper":  raw.bb_upper,
            "middle": raw.bb_middle,
            "lower":  raw.bb_lower,
        }
    else:
        data.bbands = None

    # Stochastic packaged as a dict
    if raw.stoch_k is not None:
        data.stoch = {
            "k": raw.stoch_k,
            "d": raw.stoch_d,
        }
    else:
        data.stoch = None

    # ── Derived: ATR as % of price ─────────────────────────────────────────
    if data.atr is not None and data.close and data.close > 0:
        data.atr_pct = data.atr / data.close
    else:
        data.atr_pct = None

    # ── Derived: Price vs moving averages ──────────────────────────────────
    if data.close is not None and data.sma_20 is not None:
        data.price_above_sma = data.close > data.sma_20
    if data.close is not None and data.ema_20 is not None:
        data.price_above_ema = data.close > data.ema_20

    # ── Derived: Bollinger Band position (0.0 to 1.0+) ────────────────────
    if (data.bbands is not None
            and data.close is not None
            and data.bbands["upper"] is not None
            and data.bbands["lower"] is not None):
        band_range = data.bbands["upper"] - data.bbands["lower"]
        if band_range > 0:
            data.bb_position = (data.close - data.bbands["lower"]) / band_range
        else:
            data.bb_position = 0.5  # degenerate band — treat as neutral

    # ── Data completeness ──────────────────────────────────────────────────
    critical_fields = {
        "rsi":    data.rsi,
        "macd":   data.macd,
        "adx":    data.adx,
        "atr":    data.atr,
        "sma_20": data.sma_20,
        "ema_20": data.ema_20,
        "bbands": data.bbands,
        "stoch":  data.stoch,
        "obv":    data.obv,
        "close":  data.close,
    }
    present = [v for v in critical_fields.values() if v is not None]
    data.data_completeness = len(present) / len(critical_fields)
    data.missing_indicators = [k for k, v in critical_fields.items() if v is None]

    if data.missing_indicators:
        logger.debug(
            "%s/%s — missing indicators: %s",
            raw.ticker, raw.interval, data.missing_indicators
        )

    return data


# ─────────────────────────────────────────────────────────────────────────────
# NORMALIZATION FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────
# Each function converts one raw indicator value into a 0-100 quality score.
# Higher score = better setup for a swing trade entry.


def normalize_rsi(rsi: Optional[float]) -> float:
    """
    RSI (Relative Strength Index) → 0-100 entry quality score.

    RSI measures buying and selling pressure on a 0-100 scale.
    For swing trading, the sweet spot is 40-65:
        • Below 40: momentum has been weak; may still be falling
        • 40-65:    "Goldilocks zone" — not too hot, not too cold (score = 100)
        • 65-75:    Getting hot; still OK but entry quality dropping
        • Above 75: Overbought — late entry, high reversal risk (score drops to 0)

    Plain English: Imagine a stock that went up 10 days in a row — its RSI
    would be very high (say, 85). Buying at RSI 85 means everyone who was
    going to buy has already bought. You are the "last buyer", and the stock
    has no one left to push it higher. This is why high RSI = lower score.
    """
    if rsi is None:
        return 50.0  # no data = neutral opinion

    # Sweet spot: RSI 40-65 = perfect
    if config.RSI_IDEAL_LOW <= rsi <= config.RSI_IDEAL_HIGH:
        return 100.0

    # Approaching ideal from below (RSI 30-40): linearly ramp 40 → 100
    if config.RSI_OVERSOLD <= rsi < config.RSI_IDEAL_LOW:
        slope = (100.0 - 40.0) / (config.RSI_IDEAL_LOW - config.RSI_OVERSOLD)
        return 40.0 + slope * (rsi - config.RSI_OVERSOLD)

    # Above ideal, heading toward overbought (RSI 65-75): linearly decay 100 → 40
    if config.RSI_IDEAL_HIGH < rsi <= config.RSI_OVERBOUGHT:
        slope = (100.0 - 40.0) / (config.RSI_OVERBOUGHT - config.RSI_IDEAL_HIGH)
        return 100.0 - slope * (rsi - config.RSI_IDEAL_HIGH)

    # Deeply overbought (RSI 75-100): decay 40 → 0
    if rsi > config.RSI_OVERBOUGHT:
        slope = 40.0 / (100.0 - config.RSI_OVERBOUGHT)
        return max(0.0, 40.0 - slope * (rsi - config.RSI_OVERBOUGHT))

    # Deeply oversold (RSI 0-30): decay 40 → 0
    if rsi < config.RSI_OVERSOLD:
        slope = 40.0 / config.RSI_OVERSOLD
        return max(0.0, slope * rsi)

    return 50.0  # should never reach here


def normalize_macd_histogram(
    histogram: Optional[float],
    close: Optional[float],
) -> float:
    """
    MACD Histogram → 0-100 momentum score.

    The MACD histogram is the difference between the MACD line and its
    signal line. When positive and growing, it means buying momentum is
    accelerating. When negative, selling momentum is in control.

    Plain English: Think of the MACD histogram as a speedometer for
    the price trend. Positive and growing = accelerating upward.
    Negative = slowing down or reversing.

    We normalize by price to make the score comparable across different
    stock prices (a histogram of $0.40 is huge for a $10 stock but tiny
    for a $500 stock).
    """
    if histogram is None:
        return 50.0  # no data = neutral

    if close is None or close <= 0:
        # Can't normalize by price; use magnitude directly with a rough cap
        if histogram >= 0.5:
            return 100.0
        if histogram >= 0:
            return 50.0 + (histogram / 0.5) * 50.0
        if histogram >= -0.5:
            return 50.0 + (histogram / 0.5) * 50.0  # negative side: 0 → 50
        return 0.0

    # Histogram as % of stock price, scaled by 1000 for readability
    # Example: histogram=1.20, close=186.50 → hp = 6.44
    hp = (histogram / close) * 1000

    # Strong positive histogram (hp ≥ 0.5): full score
    if hp >= 0.5:
        return 100.0

    # Positive but weak (0 to 0.5): score 50 → 100
    if hp >= 0:
        return 50.0 + (hp / 0.5) * 50.0

    # Negative (selling pressure, -0.5 to 0): score 0 → 50
    if hp >= -0.5:
        return 50.0 + (hp / 0.5) * 50.0

    # Strongly negative (hp < -0.5): score = 0
    return 0.0


def normalize_adx(adx: Optional[float]) -> float:
    """
    ADX (Average Directional Index) → 0-100 trend strength score.

    ADX measures HOW STRONG a trend is — not whether it is up or down.
    A rising price with a high ADX means the uptrend is powerful.
    A rising price with a low ADX means it could reverse at any moment.

    Plain English: Imagine a river current. ADX measures how fast the
    water is moving. A high ADX (> 25) = fast current = price will likely
    keep trending. Low ADX (< 15) = still water = price is just drifting.

    We use price vs moving averages to determine DIRECTION, and ADX
    to determine STRENGTH.
    """
    if adx is None:
        return 50.0  # no data = neutral

    # Very strong trend (ADX ≥ 40): full score
    if adx >= config.ADX_VERY_STRONG:
        return 100.0

    # Strong trend (ADX 25-40): score 60 → 100
    if adx >= config.ADX_STRONG:
        slope = (100.0 - 60.0) / (config.ADX_VERY_STRONG - config.ADX_STRONG)
        return 60.0 + slope * (adx - config.ADX_STRONG)

    # Developing trend (ADX 15-25): score 20 → 60
    if adx >= config.ADX_WEAK:
        slope = (60.0 - 20.0) / (config.ADX_STRONG - config.ADX_WEAK)
        return 20.0 + slope * (adx - config.ADX_WEAK)

    # Weak / no trend (ADX < 15): score 0 → 20
    slope = 20.0 / config.ADX_WEAK
    return max(0.0, slope * adx)


def normalize_ma_alignment(
    close: Optional[float],
    sma_20: Optional[float],
    ema_20: Optional[float],
) -> float:
    """
    Price vs Moving Averages → 0-100 trend alignment score.

    When a stock's price is above its 20-day SMA and EMA, it is in a
    short-term uptrend. The further above (up to ~3%), the more bullish.

    Plain English: Moving averages are like the "average mood" of the stock
    over the past 20 days. If today's price is above the average, the stock
    is "happier than usual" — a bullish signal. If it's below, the stock
    is trending down.
    """
    if close is None:
        return 50.0

    above_sma = close > sma_20 if sma_20 is not None else None
    above_ema = close > ema_20 if ema_20 is not None else None

    if above_sma is None and above_ema is None:
        return 50.0  # no MA data at all

    if above_sma is True and above_ema is True:
        # Calculate how far above (bonus for being confidently above, not teetering)
        # Bonus maxes out at 3% above both MAs
        sma_pct = (close - sma_20) / sma_20 if sma_20 else 0.0
        ema_pct = (close - ema_20) / ema_20 if ema_20 else 0.0
        avg_pct = (sma_pct + ema_pct) / 2
        # Base score 70; bonus up to 30 for being 3%+ above
        bonus = min(30.0, (avg_pct / 0.03) * 30.0)
        return min(100.0, 70.0 + bonus)

    if above_sma is False and above_ema is False:
        # Below both MAs — bearish
        sma_pct = (sma_20 - close) / sma_20 if sma_20 else 0.0
        ema_pct = (ema_20 - close) / ema_20 if ema_20 else 0.0
        avg_pct = (sma_pct + ema_pct) / 2
        # Base score 30; drops further as price falls below MAs
        penalty = min(30.0, (avg_pct / 0.03) * 30.0)
        return max(0.0, 30.0 - penalty)

    # Mixed (above one, below the other) — transition zone
    return 50.0


def normalize_bb_position(
    close: Optional[float],
    bb_upper: Optional[float],
    bb_lower: Optional[float],
) -> float:
    """
    Bollinger Band position → 0-100 entry timing score.

    Where is the price within its Bollinger Band channel?

    0.0 = at lower band (possible support, good for pullback entries)
    0.3-0.5 = lower-to-middle (ideal entry zone in an uptrend)
    0.5-0.7 = middle-to-upper (still OK, starting to extend)
    1.0+ = at or above upper band (too extended, high reversal risk)

    Plain English: Bollinger Bands are like guardrails on a highway.
    Buying when the price is near the lower guardrail (in an uptrend)
    gives you more room to profit before hitting the upper guardrail.
    Buying near the upper band means you might immediately run into resistance.
    """
    if close is None or bb_upper is None or bb_lower is None:
        return 50.0

    band_range = bb_upper - bb_lower
    if band_range <= 0:
        return 50.0

    bb_pct = (close - bb_lower) / band_range

    # Above upper band: extended move, high reversal risk
    if bb_pct >= 1.0:
        return 0.0

    # Near upper band (70-100% of band): extended, score drops
    if bb_pct >= config.BB_EXTENDED_LOW:
        slope = 60.0 / (1.0 - config.BB_EXTENDED_LOW)
        return max(0.0, 60.0 - slope * (bb_pct - config.BB_EXTENDED_LOW))

    # Ideal zone (30-50% of band): perfect entry timing
    if config.BB_IDEAL_LOW <= bb_pct <= config.BB_IDEAL_HIGH:
        return 100.0

    # Above ideal to extended (50-70%): score 100 → 60
    if config.BB_IDEAL_HIGH < bb_pct < config.BB_EXTENDED_LOW:
        slope = (100.0 - 60.0) / (config.BB_EXTENDED_LOW - config.BB_IDEAL_HIGH)
        return 100.0 - slope * (bb_pct - config.BB_IDEAL_HIGH)

    # Approaching ideal from below (15-30%): score 80 → 100
    if 0.15 <= bb_pct < config.BB_IDEAL_LOW:
        slope = (100.0 - 80.0) / (config.BB_IDEAL_LOW - 0.15)
        return 80.0 + slope * (bb_pct - 0.15)

    # Near lower band or below (0-15%): score 40 → 80
    if 0.0 <= bb_pct < 0.15:
        slope = (80.0 - 40.0) / 0.15
        return 40.0 + slope * bb_pct

    # Below lower band
    return 0.0


def normalize_stochastic(stoch_k: Optional[float]) -> float:
    """
    Stochastic K → 0-100 entry quality score.

    The Stochastic oscillator shows where the current price sits relative to
    its recent high-low range. Like RSI, extreme values signal risk:
        • K > 80: overbought (price has been unusually high lately)
        • K < 20: oversold (price has been unusually low lately)
        • K 30-70: normal range — most swing trade entries happen here

    Plain English: If a stock closed at the very TOP of its range every day
    this week, its Stochastic would be near 100 — it is "overextended".
    Buying here means the stock may be exhausted and ready to pull back.
    """
    if stoch_k is None:
        return 50.0

    # Extreme overbought (> 85): significant penalty
    if stoch_k > config.STOCH_EXTREME_OB:
        falloff = (stoch_k - config.STOCH_EXTREME_OB) / (100.0 - config.STOCH_EXTREME_OB)
        return max(0.0, 40.0 - falloff * 40.0)

    # Overbought (70-85): score 60 → 40
    if stoch_k > config.STOCH_OVERBOUGHT:
        slope = (60.0 - 40.0) / (config.STOCH_EXTREME_OB - config.STOCH_OVERBOUGHT)
        return 60.0 - slope * (stoch_k - config.STOCH_OVERBOUGHT)

    # Ideal zone (30-70): score peaks at 50 = 100
    if config.STOCH_OVERSOLD <= stoch_k <= config.STOCH_OVERBOUGHT:
        # Score peaks at midpoint (~50), falls toward edges
        deviation = abs(stoch_k - 50.0) / 30.0
        return max(60.0, 100.0 - deviation * 40.0)

    # Oversold (20-30): could be a reversal setup — treat as moderate
    if config.STOCH_OVERSOLD * 0.5 <= stoch_k < config.STOCH_OVERSOLD:
        slope = (60.0 - 50.0) / (config.STOCH_OVERSOLD - config.STOCH_OVERSOLD * 0.5)
        return 50.0 + slope * (stoch_k - config.STOCH_OVERSOLD * 0.5)

    # Deeply oversold (< 10): possible bottoming, but risky
    return max(30.0, stoch_k * 2.0)


def normalize_atr_pct(atr_pct: Optional[float]) -> float:
    """
    ATR% (ATR as fraction of price) → 0-100 volatility quality score.

    THIS SCORE IS INVERSELY SCALED: lower volatility = higher score.

    Why penalize high ATR? Because high ATR forces you to use wider
    stop-losses (to avoid being stopped out by normal noise). Wider stops
    mean more risk per trade, which means either smaller position sizes or
    more money at risk.

    Plain English: If a $100 stock moves $8/day on average (ATR%=8%),
    you would need a stop-loss at least $8-12 below your entry. To risk
    only $500, you could only buy 40-60 shares. This limits profit potential
    and makes precise risk management very difficult.

    ATR% scoring:
        ≤ 1.5%: Excellent — tight, manageable range → score 100
        1.5-3%: Good      — normal market volatility → score 70-100
        3-6%:   High      — getting risky, widens stops → score 0-70
        > 6%:   Dangerous — approaching hard filter limit → score 0
    """
    if atr_pct is None:
        return 50.0  # no data = neutral

    # Very low volatility: great for risk management
    if atr_pct <= config.ATR_PCT_IDEAL:
        return 100.0

    # Normal volatility range: score 100 → 70
    if atr_pct <= config.ATR_PCT_HIGH:
        slope = (100.0 - 70.0) / (config.ATR_PCT_HIGH - config.ATR_PCT_IDEAL)
        return 100.0 - slope * (atr_pct - config.ATR_PCT_IDEAL)

    # High volatility: score 70 → 0
    if atr_pct <= config.ATR_PCT_MAX:
        slope = 70.0 / (config.ATR_PCT_MAX - config.ATR_PCT_HIGH)
        return max(0.0, 70.0 - slope * (atr_pct - config.ATR_PCT_HIGH))

    # Excessive volatility (also triggers hard filter at 8%)
    return 0.0


def compute_obv_score(
    daily: "IndicatorData",
    h4: Optional["IndicatorData"] = None,
) -> float:
    """
    OBV (On-Balance Volume) → 0-100 volume confirmation score.

    OBV adds volume on up days and subtracts it on down days. A rising OBV
    confirms that buyers are stepping in with conviction. A falling OBV
    during a price rise is a warning sign (price may be rising on thin air).

    Plain English: Imagine a boat rising in water. If the tide (volume) is
    coming in (OBV rising), the boat will keep rising. If the tide is going
    out, the boat will eventually drop even if it looks fine today.

    LIMITATION NOTE: TAAPI returns a single OBV value, not a historical
    series. We cannot compute the slope directly. Instead, we use proxy
    signals: OBV sign + price vs MA alignment + MACD direction.
    The confidence score reflects this limitation.
    """
    if daily is None:
        return 50.0

    score = 50.0  # start at neutral

    # OBV sign: positive = more buying volume historically = bullish
    if daily.obv is not None:
        if daily.obv > 0:
            score += 15.0
        elif daily.obv < 0:
            score -= 15.0

    # Price above moving averages (suggests OBV is likely rising with price)
    if daily.price_above_sma is True and daily.price_above_ema is True:
        score += 20.0
    elif daily.price_above_sma is False and daily.price_above_ema is False:
        score -= 20.0

    # MACD histogram positive = buying momentum = OBV likely rising
    if daily.macd and daily.macd.get("histogram") is not None:
        hist = daily.macd["histogram"]
        if hist > 0:
            score += 15.0
        elif hist < 0:
            score -= 15.0

    # 4h OBV agreement (if available): extra confirmation
    if h4 is not None and h4.obv is not None and daily.obv is not None:
        # Both positive or both negative = agreement
        if (h4.obv > 0) == (daily.obv > 0):
            score += 10.0
        else:
            score -= 5.0

    return max(0.0, min(100.0, score))


def compute_mtf_score(
    daily: "IndicatorData",
    h4: Optional["IndicatorData"],
) -> float:
    """
    Multi-timeframe agreement → 0-100 confirmation score.

    We compare three signals between the daily and 4-hour charts:
        1. RSI direction (both above 50 = bullish, or both below)
        2. MACD histogram sign (both positive = bullish momentum on both charts)
        3. Price vs SMA (both above = uptrend confirmed on both timeframes)

    More agreement = higher confidence = higher score.

    Plain English: If the daily chart says "uptrend" and the 4-hour chart
    also says "uptrend", you have two independent sources of evidence.
    If they disagree, you have a mixed signal — best to wait for clarity.
    """
    if h4 is None:
        logger.debug("No 4h data for %s — MTF score defaults to 50", daily.ticker)
        return 50.0

    agreements = 0
    total_checks = 0

    # Check 1: RSI direction (> 50 = bullish bias)
    if daily.rsi is not None and h4.rsi is not None:
        total_checks += 1
        if (daily.rsi > 50) == (h4.rsi > 50):
            agreements += 1

    # Check 2: MACD histogram sign
    daily_hist = daily.macd.get("histogram") if daily.macd else None
    h4_hist    = h4.macd.get("histogram") if h4.macd else None
    if daily_hist is not None and h4_hist is not None:
        total_checks += 1
        if (daily_hist > 0) == (h4_hist > 0):
            agreements += 1

    # Check 3: Price above SMA
    if daily.price_above_sma is not None and h4.price_above_sma is not None:
        total_checks += 1
        if daily.price_above_sma == h4.price_above_sma:
            agreements += 1

    if total_checks == 0:
        return 50.0  # no overlapping data to compare

    agreement_ratio = agreements / total_checks

    # Scale: 0 agreement = 0, partial = proportional, full = 100
    # Apply a steeper curve: partial agreement still gives moderate score
    return round(agreement_ratio * 100.0, 1)
