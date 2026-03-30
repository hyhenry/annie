"""
scoring.py — The brain of the engine.

This module takes clean IndicatorData (from indicators.py) and produces a
final score, recommendation, and confidence level for each stock.

THE SCORING PIPELINE (in order):
    1. Compute 6 factor sub-scores (trend, momentum, volume, entry_timing,
       volatility, multi_timeframe) — each 0 to 100.
    2. Compute a penalty score (0 to 100) for conflicts and missing data.
    3. Apply hard filters (price, volume, ATR%). If any fail → auto "Avoid".
    4. Weighted-sum the factor scores minus the penalty deduction.
    5. Map the final score to Buy / Watch / Avoid.
    6. Compute a confidence score (0-100) based on data completeness and
       signal agreement.
    7. Generate a plain-English explanation.

THE MATH (in plain English):
    final_score = (trend × 0.25) + (momentum × 0.20) + (volume × 0.15)
                + (entry_timing × 0.15) + (volatility × 0.10)
                + (multi_timeframe × 0.10)
                − (penalty × 0.05)

    The maximum deduction from penalties is 5 points (100 × 0.05).
    This keeps the penalty system from completely dominating the score.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

import config
from indicators import (
    IndicatorData,
    normalize_rsi,
    normalize_macd_histogram,
    normalize_adx,
    normalize_ma_alignment,
    normalize_bb_position,
    normalize_stochastic,
    normalize_atr_pct,
    compute_obv_score,
    compute_mtf_score,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FactorScores:
    """
    Individual sub-scores for each scoring factor.
    Each is 0-100 before weighting. Included in output JSON.
    """
    trend:           float = 0.0   # Price trend quality (SMA/EMA + ADX)
    momentum:        float = 0.0   # Buying pressure speed (MACD + RSI)
    volume:          float = 0.0   # Volume confirmation (OBV proxy)
    entry_timing:    float = 0.0   # Is this a good entry point? (BBands + Stoch)
    volatility:      float = 0.0   # Is volatility manageable? (ATR%)
    multi_timeframe: float = 0.0   # Daily and 4h agreement
    risk_penalty:    float = 0.0   # Penalty score (higher = bigger deduction)


@dataclass
class ScoringResult:
    """
    Complete scoring output for one stock.
    This is assembled by score_ticker() and converted to JSON by engine.py.
    """
    ticker:          str
    raw_score:       float         # Weighted sum before penalty deduction
    final_score:     float         # 0-100 after penalty
    recommendation:  str           # "Buy", "Watch", or "Avoid"
    confidence:      float         # 0-100 — how reliable is this recommendation?
    factor_scores:   FactorScores
    filters_passed:  bool
    filter_failures: List[str] = field(default_factory=list)
    explanation:     str = ""
    risk_note:       str = ""      # Plain-English risk warning


# ─────────────────────────────────────────────────────────────────────────────
# HARD FILTERS
# ─────────────────────────────────────────────────────────────────────────────

def apply_hard_filters(data: IndicatorData) -> tuple:
    """
    Check all hard filters against indicator data.

    Returns:
        (passed: bool, failures: list[str])
        If any filter fails, the stock should be classified as "Avoid"
        regardless of its score.
    """
    failures: List[str] = []

    # ── Price filter ──────────────────────────────────────────────────────
    # Stocks below $5 are excluded regardless of technical signals.
    if data.close is not None and data.close < config.HARD_FILTERS["min_price"]:
        failures.append(
            f"price_below_minimum (${data.close:.2f} < ${config.HARD_FILTERS['min_price']:.2f})"
        )

    # ── Volume filter ──────────────────────────────────────────────────────
    # Low volume means the stock is hard to trade without moving the price.
    if data.volume is not None and data.volume < config.HARD_FILTERS["min_avg_volume"]:
        failures.append(
            f"volume_below_minimum ({data.volume:,.0f} < {config.HARD_FILTERS['min_avg_volume']:,})"
        )

    # ── Volatility filter (ATR%) ───────────────────────────────────────────
    # Extreme volatility makes stop-loss placement impractical.
    if data.atr_pct is not None and data.atr_pct > config.HARD_FILTERS["max_atr_pct"]:
        failures.append(
            f"atr_too_high ({data.atr_pct:.1%} > {config.HARD_FILTERS['max_atr_pct']:.1%})"
        )

    # ── Missing price (cannot trade without a price) ───────────────────────
    if data.close is None:
        failures.append("missing_close_price")

    passed = len(failures) == 0
    return passed, failures


# ─────────────────────────────────────────────────────────────────────────────
# FACTOR SCORES
# ─────────────────────────────────────────────────────────────────────────────

def _compute_trend_score(daily: IndicatorData) -> float:
    """
    Trend score = 60% MA alignment + 40% ADX strength.

    A strong trend has BOTH a price above its moving averages AND a high ADX.
    ADX tells us the trend has conviction; MA alignment tells us the direction.
    """
    ma_score  = normalize_ma_alignment(daily.close, daily.sma_20, daily.ema_20)
    adx_score = normalize_adx(daily.adx)
    return (ma_score * 0.60) + (adx_score * 0.40)


def _compute_momentum_score(daily: IndicatorData) -> float:
    """
    Momentum score = 60% RSI + 40% MACD histogram.

    Momentum is about DIRECTION and SPEED of price movement.
    RSI tells us if the move is overextended. MACD histogram shows acceleration.
    """
    rsi_score  = normalize_rsi(daily.rsi)
    hist       = daily.macd.get("histogram") if daily.macd else None
    macd_score = normalize_macd_histogram(hist, daily.close)
    return (rsi_score * 0.60) + (macd_score * 0.40)


def _compute_entry_timing_score(daily: IndicatorData) -> float:
    """
    Entry timing score = 55% Bollinger Band position + 45% Stochastic K.

    "Entry timing" asks: is this a good MOMENT to enter, or are we chasing?
    Good timing = price pulled back toward the MA, not extended at the top.
    """
    stoch_k    = daily.stoch.get("k") if daily.stoch else None
    bb_score   = normalize_bb_position(daily.close, daily.bbands and daily.bbands.get("upper"),
                                       daily.bbands and daily.bbands.get("lower"))
    stoch_score = normalize_stochastic(stoch_k)
    return (bb_score * 0.55) + (stoch_score * 0.45)


def compute_factor_scores(
    daily: IndicatorData,
    h4: Optional[IndicatorData] = None,
) -> FactorScores:
    """
    Compute all 7 factor scores for a stock.

    Args:
        daily: Primary timeframe indicator data (typically daily)
        h4:    Secondary timeframe data (4h) — used for multi-timeframe score.
               If None, multi-timeframe score defaults to 50 (neutral).

    Returns:
        FactorScores with all 7 components filled in.
    """
    scores = FactorScores()

    scores.trend          = round(_compute_trend_score(daily), 1)
    scores.momentum       = round(_compute_momentum_score(daily), 1)
    scores.volume         = round(compute_obv_score(daily, h4), 1)
    scores.entry_timing   = round(_compute_entry_timing_score(daily), 1)
    scores.volatility     = round(normalize_atr_pct(daily.atr_pct), 1)
    scores.multi_timeframe = round(compute_mtf_score(daily, h4), 1)
    scores.risk_penalty   = round(_compute_penalty_score(daily, h4), 1)

    logger.debug(
        "%s factor scores: trend=%.1f momentum=%.1f volume=%.1f timing=%.1f "
        "vol=%.1f mtf=%.1f penalty=%.1f",
        daily.ticker, scores.trend, scores.momentum, scores.volume,
        scores.entry_timing, scores.volatility, scores.multi_timeframe, scores.risk_penalty
    )

    return scores


# ─────────────────────────────────────────────────────────────────────────────
# PENALTY CALCULATION
# ─────────────────────────────────────────────────────────────────────────────

def _compute_penalty_score(
    daily: IndicatorData,
    h4: Optional[IndicatorData] = None,
) -> float:
    """
    Accumulate penalty points for warning conditions.

    The penalty score is 0-100. It is multiplied by FACTOR_WEIGHTS['risk_penalty']
    (0.05) to produce the deduction. Maximum deduction = 5 points.

    This is NOT meant to disqualify stocks on its own — the hard filters do that.
    This is a "caution" signal that slightly reduces scores for risky setups.
    """
    points = 0.0

    # RSI overbought: buying at the top increases reversal risk
    if daily.rsi is not None and daily.rsi > config.RSI_OVERBOUGHT:
        points += config.PENALTY_POINTS["rsi_overbought"]
        logger.debug("%s: penalty — RSI overbought (%.1f)", daily.ticker, daily.rsi)

    # RSI oversold: catching a falling knife
    elif daily.rsi is not None and daily.rsi < config.RSI_OVERSOLD:
        points += config.PENALTY_POINTS["rsi_oversold"]
        logger.debug("%s: penalty — RSI oversold (%.1f)", daily.ticker, daily.rsi)

    # Stochastic extreme overbought
    stoch_k = daily.stoch.get("k") if daily.stoch else None
    if stoch_k is not None and stoch_k > config.STOCH_EXTREME_OB:
        points += config.PENALTY_POINTS["stoch_extreme_overbought"]
        logger.debug("%s: penalty — Stochastic extreme overbought (%.1f)", daily.ticker, stoch_k)

    # MACD bearish while price is above SMA (divergence warning)
    # This means price is up but momentum is not confirming
    hist = daily.macd.get("histogram") if daily.macd else None
    if (hist is not None and hist < 0
            and daily.price_above_sma is True):
        points += config.PENALTY_POINTS["macd_bearish_in_uptrend"]
        logger.debug("%s: penalty — bearish MACD while price above SMA", daily.ticker)

    # Multi-timeframe MACD conflict (daily and 4h disagree on direction)
    if h4 is not None:
        h4_hist = h4.macd.get("histogram") if h4.macd else None
        if (hist is not None and h4_hist is not None
                and (hist > 0) != (h4_hist > 0)):
            points += config.PENALTY_POINTS["mtf_macd_conflict"]
            logger.debug("%s: penalty — MTF MACD conflict (1d vs 4h)", daily.ticker)

    # Missing critical data
    if daily.rsi is None:
        points += config.PENALTY_POINTS["missing_rsi"]
    if daily.macd is None:
        points += config.PENALTY_POINTS["missing_macd"]
    if daily.adx is None:
        points += config.PENALTY_POINTS["missing_adx"]

    # MA recently crossed: price between SMA and EMA (uncertain trend direction)
    if (daily.close is not None
            and daily.sma_20 is not None
            and daily.ema_20 is not None):
        min_ma = min(daily.sma_20, daily.ema_20)
        max_ma = max(daily.sma_20, daily.ema_20)
        if min_ma < daily.close < max_ma:
            points += config.PENALTY_POINTS["ma_recently_crossed"]
            logger.debug("%s: penalty — price between SMA and EMA (MA crossover zone)", daily.ticker)

    return min(100.0, points)


# ─────────────────────────────────────────────────────────────────────────────
# TOTAL SCORE CALCULATION
# ─────────────────────────────────────────────────────────────────────────────

def compute_total_score(scores: FactorScores) -> tuple:
    """
    Compute the final weighted score from factor scores.

    Formula:
        raw_score = (trend × 0.25) + (momentum × 0.20) + (volume × 0.15)
                  + (entry_timing × 0.15) + (volatility × 0.10)
                  + (multi_timeframe × 0.10)

        deduction = risk_penalty × 0.05     (max = 5 points)

        final_score = clamp(raw_score - deduction, 0, 100)

    Returns:
        (raw_score: float, final_score: float)
    """
    weights = config.FACTOR_WEIGHTS

    raw_score = (
        scores.trend           * weights["trend"]
        + scores.momentum      * weights["momentum"]
        + scores.volume        * weights["volume"]
        + scores.entry_timing  * weights["entry_timing"]
        + scores.volatility    * weights["volatility"]
        + scores.multi_timeframe * weights["multi_timeframe"]
    )

    deduction  = scores.risk_penalty * weights["risk_penalty"]
    final_score = max(0.0, min(100.0, raw_score - deduction))

    return round(raw_score, 1), round(final_score, 1)


# ─────────────────────────────────────────────────────────────────────────────
# RECOMMENDATION MAPPING
# ─────────────────────────────────────────────────────────────────────────────

def get_recommendation(score: float, filters_passed: bool) -> str:
    """
    Map a final score to a recommendation label.

    Rules:
        • If any hard filter failed: always "Avoid" regardless of score.
        • Score >= 80: "Buy"
        • Score >= 60: "Watch"
        • Score < 60:  "Avoid"

    The thresholds are configurable in config.RECOMMENDATION_BANDS.
    """
    if not filters_passed:
        return "Avoid"

    if score >= config.RECOMMENDATION_BANDS["Buy"]:
        return "Buy"
    if score >= config.RECOMMENDATION_BANDS["Watch"]:
        return "Watch"
    return "Avoid"


# ─────────────────────────────────────────────────────────────────────────────
# CONFIDENCE SCORE
# ─────────────────────────────────────────────────────────────────────────────

def compute_confidence(
    daily: IndicatorData,
    factor_scores: FactorScores,
) -> float:
    """
    Confidence score (0-100) = how much to trust this recommendation.

    Three components:
        1. Data completeness (40%): Did all 10 indicators return data?
           10/10 = perfect, 7/10 = 70% → scales to 0-100.

        2. Signal agreement (35%): Do the 5 directional factor scores agree?
           All bullish (all > 60) or all bearish (all < 40) = full agreement.
           Mixed signals = lower confidence.

        3. ADX strength (25%): A strong trend (high ADX) makes signals more
           reliable. A weak ADX means even bullish indicators may not hold.

    Plain English: Think of confidence like how sure a doctor is about a
    diagnosis. If they have all the test results, the tests agree, and the
    patient has a clear and consistent medical history, confidence is high.
    If some tests came back blank and others contradict each other, confidence
    is low even if the overall picture leans one direction.
    """
    weights = config.CONFIDENCE_WEIGHTS

    # ── Component 1: Data completeness ────────────────────────────────────
    completeness = daily.data_completeness  # already 0-1

    # ── Component 2: Signal agreement ────────────────────────────────────
    # Measure how far the directional factors are from a "50/50 split"
    # 5/5 bullish (or 0/5) = unanimity = 1.0
    # 2/5 or 3/5 = split = 0.0
    directional = [
        factor_scores.trend,
        factor_scores.momentum,
        factor_scores.volume,
        factor_scores.entry_timing,
        factor_scores.multi_timeframe,
    ]
    bullish_count = sum(1 for s in directional if s >= 60.0)
    # Deviation from 50/50 split: ranges 0 (equal split) to 1 (unanimous)
    deviation = abs(bullish_count - 2.5) / 2.5
    agreement = deviation

    # ── Component 3: ADX strength ─────────────────────────────────────────
    adx_norm = min(1.0, (daily.adx or 0.0) / config.ADX_STRONG)

    # ── Combine ───────────────────────────────────────────────────────────
    confidence = (
        completeness * weights["data_completeness"]
        + agreement  * weights["signal_agreement"]
        + adx_norm   * weights["adx_strength"]
    ) * 100.0

    return round(min(100.0, max(0.0, confidence)), 1)


# ─────────────────────────────────────────────────────────────────────────────
# EXPLANATION GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

# Templates for each factor, keyed by score range.
# The engine picks the right sentence based on the sub-score.
_TREND_TEMPLATES = [
    (80, "The trend is strong — ADX of {adx:.0f} confirms the uptrend, and price is well above its moving averages."),
    (60, "The trend is moderately strong — price is above its moving averages, though with some uncertainty."),
    (40, "The trend is mixed — price is hovering near its moving averages without clear direction."),
    (0,  "The trend is weak or negative — price is below its moving averages, suggesting downward pressure."),
]
_MOMENTUM_TEMPLATES = [
    (80, "Momentum is healthy: RSI of {rsi:.1f} is in the ideal swing-trade zone (40–65), and MACD confirms buying pressure."),
    (60, "Momentum is decent but slightly outside the ideal RSI zone — watch for continuation."),
    (40, "Momentum signals are mixed — RSI may be approaching overbought or oversold territory."),
    (0,  "Momentum is unfavourable — RSI or MACD suggests the stock is overextended or under selling pressure."),
]
_VOLUME_TEMPLATES = [
    (70, "Volume is confirming the move — OBV signals suggest buyers are in control."),
    (40, "Volume confirmation is neutral — no strong buying or selling pressure visible."),
    (0,  "Volume is not confirming the move — this is a caution signal; price may not hold."),
]
_TIMING_TEMPLATES = [
    (80, "Entry timing looks good — price has pulled back toward its moving average and Bollinger midline."),
    (60, "Entry timing is acceptable — price is not too extended, though not at an ideal pullback level."),
    (40, "Entry timing is marginal — price is somewhat extended from its average levels."),
    (0,  "Entry timing is poor — price is near the top of its Bollinger Band or deeply oversold."),
]
_VOLATILITY_TEMPLATES = [
    (80, "Volatility is low and manageable — stop-losses can be placed tightly, limiting risk."),
    (55, "Volatility is moderate — ATR is within acceptable range for swing trading."),
    (0,  "Volatility is elevated — daily price swings are large, which makes stop-losses wider and risk harder to control."),
]
_MTF_TEMPLATES = [
    (70, "Multiple timeframes agree — both the daily and 4-hour charts show consistent signals."),
    (40, "Timeframe signals are partially aligned — some agreement between daily and 4-hour, but not unanimous."),
    (0,  "Timeframes conflict — the daily and 4-hour charts are sending contradictory signals. Exercise extra caution."),
]


def _pick_template(score: float, templates: list) -> str:
    """Return the template sentence for the given score (highest threshold that score meets)."""
    for threshold, text in sorted(templates, reverse=True):
        if score >= threshold:
            return text
    return templates[-1][1]


def generate_explanation(
    ticker: str,
    result: "ScoringResult",
    daily: IndicatorData,
) -> str:
    """
    Generate a beginner-friendly plain-English explanation of the score.

    Selects one sentence per factor based on its sub-score, then assembles
    them into a cohesive paragraph.

    No trading jargon without explanation. No abbreviations without context.
    """
    fs = result.factor_scores
    rsi_val = daily.rsi or 0.0
    adx_val = daily.adx or 0.0

    sentences = []

    # Opening: overall verdict
    if result.recommendation == "Buy":
        sentences.append(
            f"{ticker} scored {result.final_score:.0f}/100 and is recommended as a BUY."
        )
    elif result.recommendation == "Watch":
        sentences.append(
            f"{ticker} scored {result.final_score:.0f}/100 — a WATCH signal means conditions "
            f"are improving but not yet ideal for entry."
        )
    else:
        if not result.filters_passed:
            reasons = "; ".join(result.filter_failures)
            sentences.append(
                f"{ticker} is automatically AVOIDED because it failed safety filters: {reasons}."
            )
        else:
            sentences.append(
                f"{ticker} scored {result.final_score:.0f}/100 — conditions do not support "
                f"a trade at this time."
            )

    if not result.filters_passed:
        return " ".join(sentences)

    # Factor sentences
    trend_text    = _pick_template(fs.trend,          _TREND_TEMPLATES)
    momentum_text = _pick_template(fs.momentum,        _MOMENTUM_TEMPLATES)
    volume_text   = _pick_template(fs.volume,          _VOLUME_TEMPLATES)
    timing_text   = _pick_template(fs.entry_timing,    _TIMING_TEMPLATES)
    vol_text      = _pick_template(fs.volatility,      _VOLATILITY_TEMPLATES)
    mtf_text      = _pick_template(fs.multi_timeframe, _MTF_TEMPLATES)

    sentences.append(trend_text.format(adx=adx_val, rsi=rsi_val))
    sentences.append(momentum_text.format(rsi=rsi_val, adx=adx_val))
    sentences.append(volume_text)
    sentences.append(timing_text)
    sentences.append(vol_text)
    sentences.append(mtf_text)

    # Confidence qualifier
    if result.confidence < 50:
        sentences.append(
            f"Note: confidence is low ({result.confidence:.0f}/100) because some indicators "
            f"returned no data or signals are mixed."
        )
    elif result.confidence >= 80:
        sentences.append(
            f"Confidence is high ({result.confidence:.0f}/100) — most indicators returned data "
            f"and agree with each other."
        )

    return " ".join(sentences)


# ─────────────────────────────────────────────────────────────────────────────
# MASTER FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def score_ticker(
    ticker: str,
    daily: IndicatorData,
    h4: Optional[IndicatorData] = None,
) -> ScoringResult:
    """
    Run the full scoring pipeline for one stock.

    This is the main function to call from engine.py.

    Pipeline:
        1. Apply hard filters (may force "Avoid")
        2. Compute factor scores
        3. Compute total score
        4. Determine recommendation
        5. Compute confidence
        6. Generate explanation

    Args:
        ticker: Stock symbol
        daily:  Parsed indicator data for the primary (daily) timeframe
        h4:     Parsed indicator data for the secondary (4h) timeframe (optional)

    Returns:
        ScoringResult — complete scoring output, ready to pass to risk.py
    """
    # Step 1: Hard filters
    filters_passed, filter_failures = apply_hard_filters(daily)

    # Step 2: Factor scores
    factor_scores = compute_factor_scores(daily, h4)

    # Step 3: Total score (if filters failed, score will be computed but
    # recommendation will be forced to "Avoid")
    raw_score, final_score = compute_total_score(factor_scores)

    # Step 4: Recommendation
    recommendation = get_recommendation(final_score, filters_passed)

    # Step 5: Confidence
    confidence = compute_confidence(daily, factor_scores)

    result = ScoringResult(
        ticker=ticker,
        raw_score=raw_score,
        final_score=final_score,
        recommendation=recommendation,
        confidence=confidence,
        factor_scores=factor_scores,
        filters_passed=filters_passed,
        filter_failures=filter_failures,
    )

    # Step 6: Explanation
    result.explanation = generate_explanation(ticker, result, daily)

    logger.info(
        "%s | score=%.1f | %s | confidence=%.1f | filters=%s",
        ticker, final_score, recommendation, confidence,
        "OK" if filters_passed else f"FAILED: {filter_failures}"
    )

    return result
