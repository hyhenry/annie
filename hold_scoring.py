"""
hold_scoring.py — Scoring logic for existing portfolio holdings.

TWO SCORES are computed for each holding:

    1. Hold Quality Score (0-100)
       "Is this position still healthy and worth keeping?"
       Higher = healthier. Think of it as the "green light" for staying in.

    2. Sell Risk Score (0-100)
       "How much risk is there in staying in this position?"
       Higher = more reason to consider exiting. Think of it as the "red light".

These two scores together determine the RECOMMENDATION:
    Sell          → Sell Risk very high OR hard/trailing stop breached
    Trim          → Position oversized OR extended with weakening signals
    Watch Closely → Early warning signs, not yet confirmed breakdown
    Hold          → Trend healthy, no major technical damage
    Take Profit   → Position at target with signs of stalling

────────────────────────────────────────────────────────────────────────────
HOW IS THIS DIFFERENT FROM THE OPPORTUNITY SCORE?

The Opportunity Score answers: "Is this a good stock to BUY RIGHT NOW?"
The Hold Quality Score answers: "Is this a stock I should KEEP HOLDING?"

The difference matters because:
    • You're already in — the entry timing question is irrelevant
    • You have profits (or losses) that change the math
    • Trailing stops protect gains you've already made
    • Concentration risk matters more for holders than new buyers
    • A stock can be a weak BUY but a strong HOLD (or vice versa)
────────────────────────────────────────────────────────────────────────────
"""

import logging
from typing import Dict, List, Optional, Tuple

import config
from portfolio import Holding
from indicators import (
    IndicatorData,
    normalize_adx,
    normalize_ma_alignment,
    normalize_macd_histogram,
    normalize_atr_pct,
    compute_obv_score,
    compute_mtf_score,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# HOLD QUALITY SCORE
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_rsi_for_holder(rsi: Optional[float]) -> float:
    """
    RSI sub-score tuned for an existing holder (not a new buyer).

    Differences from entry RSI scoring:
    - The "healthy zone" is wider (45-75) — you're already in and profiting
    - RSI > 80 = overextended, time to think about protection (not just penalty)
    - RSI < 45 = momentum is fading — real concern for a holder

    Plain English: If you're in a stock and RSI is 70, that's fine — the trend
    is running. But if RSI was 70 last week and is now 48, something changed.
    This score measures the current RSI health in a "holder" context.
    """
    if rsi is None:
        return 50.0

    # Healthy holding zone (45-75): score 70-100
    if 45.0 <= rsi <= 75.0:
        # Peak at 60: score = 100. Falls off toward edges.
        deviation = abs(rsi - 60.0) / 15.0  # 0 at centre, 1 at edges
        return max(70.0, 100.0 - deviation * 30.0)

    # Rising toward overbought (75-82): score falls 70 → 40
    if 75.0 < rsi <= 82.0:
        return 70.0 - ((rsi - 75.0) / 7.0) * 30.0

    # Extreme overbought (82+): score 40 → 0 (profit at risk)
    if rsi > 82.0:
        return max(0.0, 40.0 - ((rsi - 82.0) / 18.0) * 40.0)

    # Deteriorating (30-45): score 70 → 10
    if 30.0 <= rsi < 45.0:
        return 10.0 + ((rsi - 30.0) / 15.0) * 60.0

    # Deeply oversold (< 30): very bad for a holder
    return max(0.0, rsi / 30.0 * 10.0)


def _normalize_technical_position(
    close: Optional[float],
    sma_20: Optional[float],
    atr: Optional[float],
) -> float:
    """
    How "healthy" is the price relative to its moving average?

    For a holder, we want price to be modestly above the SMA — riding the trend
    without being dangerously overextended above it.

    Scoring:
        Price 0-3% above SMA: ideal — riding the trend cleanly → 85-100
        Price 3-7% above SMA: somewhat extended → 60-85
        Price 7%+ above SMA:  overextended, profit at risk → 30-60
        Price at SMA (0%):    testing support → 70
        Price 0-3% below SMA: trend threatened → 40-70
        Price 3%+ below SMA:  trend broken → 0-40
    """
    if close is None or sma_20 is None or sma_20 <= 0:
        return 50.0

    pct_diff = (close - sma_20) / sma_20

    if pct_diff > 0.07:
        # Very extended above SMA
        return max(30.0, 60.0 - ((pct_diff - 0.07) / 0.05) * 30.0)

    if pct_diff > 0.03:
        # Moderately extended
        return 60.0 + (1.0 - (pct_diff - 0.03) / 0.04) * 25.0  # 85 → 60

    if pct_diff >= 0.0:
        # Ideal zone: 0-3% above SMA
        return 85.0 + (pct_diff / 0.03) * 15.0  # 85 → 100

    if pct_diff >= -0.03:
        # Slightly below SMA (0-3%): trend threatened
        return 40.0 + ((pct_diff + 0.03) / 0.03) * 30.0  # 40 → 70

    # Significantly below SMA (> 3%): trend broken
    return max(0.0, 40.0 + (pct_diff + 0.03) / 0.07 * 40.0)


def compute_hold_quality_score(
    daily: IndicatorData,
    h4: Optional[IndicatorData] = None,
) -> Tuple[float, Dict[str, float]]:
    """
    Compute the Hold Quality Score (0-100) for an existing position.

    Higher = healthier position, safer to hold.

    Args:
        daily: Primary timeframe indicator data
        h4:    Secondary timeframe (4h) data — used for MTF score

    Returns:
        (hold_quality_score, factor_scores_dict)
    """
    weights = config.HOLD_QUALITY_WEIGHTS

    # ── Trend Integrity (30%) ──────────────────────────────────────────────
    # Is the stock still in an uptrend? Measure MA alignment + ADX strength.
    ma_score  = normalize_ma_alignment(daily.close, daily.sma_20, daily.ema_20)
    adx_score = normalize_adx(daily.adx)
    trend_integrity = (ma_score * 0.60) + (adx_score * 0.40)

    # ── Momentum Health (25%) ──────────────────────────────────────────────
    # Is buying pressure still active? RSI (holder context) + MACD histogram.
    rsi_score  = _normalize_rsi_for_holder(daily.rsi)
    hist       = daily.macd.get("histogram") if daily.macd else None
    macd_score = normalize_macd_histogram(hist, daily.close)
    momentum_health = (rsi_score * 0.60) + (macd_score * 0.40)

    # ── Volume Support (20%) ───────────────────────────────────────────────
    # Is volume still confirming the position?
    volume_support = compute_obv_score(daily, h4)

    # ── Technical Position (15%) ───────────────────────────────────────────
    # Is the price in a healthy relationship to its moving average?
    # Not too damaged (below MA) and not dangerously extended above it.
    technical_position = _normalize_technical_position(
        daily.close, daily.sma_20, daily.atr
    )

    # ── Multi-Timeframe Confirmation (10%) ────────────────────────────────
    # Do daily and 4h charts agree the trend is intact?
    mtf_score = compute_mtf_score(daily, h4)

    # ── Weighted Sum ──────────────────────────────────────────────────────
    raw = (
        trend_integrity   * weights["trend_integrity"]
        + momentum_health * weights["momentum_health"]
        + volume_support  * weights["volume_support"]
        + technical_position * weights["technical_position"]
        + mtf_score       * weights["multi_timeframe"]
    )

    hold_quality = round(max(0.0, min(100.0, raw)), 1)

    factor_scores = {
        "trend_integrity":    round(trend_integrity, 1),
        "momentum_health":    round(momentum_health, 1),
        "volume_support":     round(volume_support, 1),
        "technical_position": round(technical_position, 1),
        "multi_timeframe":    round(mtf_score, 1),
    }

    logger.debug(
        "Hold quality for daily data: %.1f (trend=%.1f mom=%.1f vol=%.1f tech=%.1f mtf=%.1f)",
        hold_quality, trend_integrity, momentum_health, volume_support,
        technical_position, mtf_score,
    )

    return hold_quality, factor_scores


# ─────────────────────────────────────────────────────────────────────────────
# SELL RISK SCORE
# ─────────────────────────────────────────────────────────────────────────────

def compute_sell_risk_score(
    holding: Holding,
    daily: IndicatorData,
    h4: Optional[IndicatorData],
    current_close: float,
    portfolio_value: float,
) -> Tuple[float, Dict]:
    """
    Compute the Sell Risk Score (0-100) for an existing position.

    Uses an additive penalty model — each warning condition adds points.
    Higher score = more reasons to consider exiting.

    Hard stop and trailing stop breaches automatically push the score to >= 80,
    ensuring an "emergency sell" recommendation whenever these are triggered.

    Args:
        holding:         The portfolio holding being evaluated
        daily:           Daily indicator data
        h4:              4-hour indicator data (optional, used for MTF early warning)
        current_close:   Current stock price
        portfolio_value: Total portfolio market value (for concentration check)

    Returns:
        (sell_risk_score, risk_factors_dict)
    """
    penalties = config.SELL_RISK_PENALTIES
    points = 0.0
    factors: Dict = {}

    # ── Hard Stop Breach ───────────────────────────────────────────────────
    # If price is below the hard stop (user-set or ATR-based), exit immediately.
    hard_stop = _compute_hard_stop(holding, daily)
    factors["hard_stop_price"] = round(hard_stop, 2) if hard_stop else None

    if hard_stop is not None and current_close < hard_stop:
        points += penalties["hard_stop_breach"]
        factors["hard_stop_breach"] = True
        logger.debug(
            "%s: hard stop breached (close=%.2f < stop=%.2f)",
            holding.ticker, current_close, hard_stop
        )
    else:
        factors["hard_stop_breach"] = False

    # ── Trailing Stop Breach ───────────────────────────────────────────────
    # Trailing stop protects locked-in profits from the peak price.
    trailing_stop = holding.trailing_stop_price(current_close)
    factors["trailing_stop_price"] = round(trailing_stop, 2)

    if current_close < trailing_stop:
        points += penalties["trailing_stop_breach"]
        factors["trailing_stop_breach"] = True
        logger.debug(
            "%s: trailing stop breached (close=%.2f < trailing=%.2f)",
            holding.ticker, current_close, trailing_stop
        )
    else:
        factors["trailing_stop_breach"] = False

    # ── Price Below SMA20 (trend breakdown) ────────────────────────────────
    if daily.price_above_sma is False:
        points += penalties["below_sma20"]
        factors["below_sma20"] = True
    else:
        factors["below_sma20"] = False

    # ── RSI Deteriorating ─────────────────────────────────────────────────
    # For a holder, RSI falling below 45 signals that momentum is turning.
    if daily.rsi is not None and daily.rsi < config.RSI_DETERIORATION_THRESHOLD:
        points += penalties["rsi_deteriorating"]
        factors["rsi_deteriorating"] = True
    else:
        factors["rsi_deteriorating"] = False

    # ── MACD Histogram Negative ────────────────────────────────────────────
    # Selling pressure is dominant — momentum is no longer on your side.
    daily_hist = daily.macd.get("histogram") if daily.macd else None
    if daily_hist is not None and daily_hist < 0:
        points += penalties["macd_histogram_negative"]
        factors["macd_negative"] = True
    else:
        factors["macd_negative"] = False

    # ── ADX Weakening ──────────────────────────────────────────────────────
    # The trend is losing its conviction — becoming choppy/directionless.
    if daily.adx is not None and daily.adx < config.ADX_DECLINE_THRESHOLD:
        points += penalties["adx_weakening"]
        factors["adx_weakening"] = True
    else:
        factors["adx_weakening"] = False

    # ── Stochastic Overbought (possibly rolling over) ──────────────────────
    # Stochastic K > 85 = extended. If K has crossed below D, it's rolling over.
    stoch_k = daily.stoch.get("k") if daily.stoch else None
    stoch_d = daily.stoch.get("d") if daily.stoch else None

    if stoch_k is not None and stoch_k > config.STOCH_EXTREME_OB:
        # K below D = the fast line has crossed under the slow line = rollover
        rolling_over = stoch_d is not None and stoch_k < stoch_d
        if rolling_over:
            points += penalties["stoch_overbought_rollover"]
            factors["stoch_overbought"] = True
            factors["stoch_rollover"] = True
        else:
            points += penalties["stoch_overbought_only"]
            factors["stoch_overbought"] = True
            factors["stoch_rollover"] = False
    else:
        factors["stoch_overbought"] = False
        factors["stoch_rollover"] = False

    # ── ATR Expanding (volatility risk) ────────────────────────────────────
    # When the daily range starts expanding, it means the market is becoming
    # uncertain — often a warning sign that a trend is ending.
    if daily.atr_pct is not None and daily.atr_pct > config.ATR_PCT_HIGH:
        points += penalties["high_volatility"]
        factors["high_volatility"] = True
    else:
        factors["high_volatility"] = False

    # ── Drawdown from Peak ─────────────────────────────────────────────────
    drawdown = holding.drawdown_from_peak(current_close)
    factors["drawdown_from_peak_pct"] = round(drawdown * 100, 1) if drawdown is not None else None

    if drawdown is not None:
        if drawdown > config.DRAWDOWN_CRITICAL_PCT:
            # 20%+ drawdown = critical — the trend may have reversed
            points += penalties["extreme_drawdown"]
            factors["extreme_drawdown"] = True
            factors["high_drawdown"] = False  # don't double-count
        elif drawdown > config.DRAWDOWN_ALARM_PCT:
            # 10-20% drawdown = warning
            points += penalties["high_drawdown"]
            factors["high_drawdown"] = True
            factors["extreme_drawdown"] = False
        else:
            factors["high_drawdown"] = False
            factors["extreme_drawdown"] = False
    else:
        factors["high_drawdown"] = False
        factors["extreme_drawdown"] = False

    # ── Concentration Risk ─────────────────────────────────────────────────
    # If this one position is > 15% of total portfolio, it's oversized.
    # Even a great stock becomes a portfolio risk if it's too large.
    if portfolio_value > 0:
        position_value = holding.shares * current_close
        position_pct   = position_value / portfolio_value
        factors["position_pct"] = round(position_pct * 100, 1)
        if position_pct > config.MAX_POSITION_PCT:
            points += penalties["concentration_risk"]
            factors["concentration_risk"] = True
        else:
            factors["concentration_risk"] = False
    else:
        factors["concentration_risk"] = False
        factors["position_pct"] = None

    # ── Multi-Timeframe Early Warning ──────────────────────────────────────
    # The 4h chart often breaks down before the daily chart.
    # If the 4h MACD is negative while the daily is still positive,
    # this is an early warning of a potential trend reversal.
    if h4 is not None:
        h4_hist = h4.macd.get("histogram") if h4.macd else None
        if (h4_hist is not None and h4_hist < 0
                and daily_hist is not None and daily_hist > 0):
            points += penalties["mtf_early_warning"]
            factors["mtf_early_warning"] = True
        else:
            factors["mtf_early_warning"] = False
    else:
        factors["mtf_early_warning"] = False

    # ── Final Score ────────────────────────────────────────────────────────
    sell_risk = min(100.0, points)

    # Force minimum sell risk of 80 if hard/trailing stop breached
    # This ensures these conditions always trigger a "Sell" recommendation
    if factors.get("hard_stop_breach") or factors.get("trailing_stop_breach"):
        sell_risk = max(sell_risk, 80.0)

    sell_risk = round(sell_risk, 1)

    logger.debug(
        "%s sell risk: %.1f points → score=%.1f | hard_stop=%s trailing_stop=%s",
        holding.ticker, points, sell_risk,
        factors.get("hard_stop_breach"), factors.get("trailing_stop_breach"),
    )

    return sell_risk, factors


# ─────────────────────────────────────────────────────────────────────────────
# RECOMMENDATION LOGIC
# ─────────────────────────────────────────────────────────────────────────────

def get_hold_recommendation(
    hold_quality: float,
    sell_risk: float,
    unrealized_pnl_pct: float,
    risk_factors: Dict,
    at_profit_target: bool,
) -> str:
    """
    Map hold quality + sell risk scores to a plain-English recommendation.

    Decision hierarchy (first matching rule wins):

        1. Sell immediately if stop is breached or sell risk is very high
        2. Take profit if near target and momentum stalling
        3. Trim if position is oversized or extended with weakening signals
        4. Watch Closely for early warning signs
        5. Hold if trend remains healthy

    Args:
        hold_quality:       Hold Quality Score (0-100, higher = healthier)
        sell_risk:          Sell Risk Score (0-100, higher = more reason to exit)
        unrealized_pnl_pct: Current unrealised gain/loss as a fraction (0.15 = 15% gain)
        risk_factors:       Dict of triggered risk conditions
        at_profit_target:   Whether price has reached or exceeded take-profit level

    Returns:
        One of: "Sell", "Trim", "Watch Closely", "Hold", "Take Profit"
    """
    # ── 1. Emergency Sell ─────────────────────────────────────────────────
    # A stop being breached is non-negotiable — exit the trade.
    if risk_factors.get("hard_stop_breach") or risk_factors.get("trailing_stop_breach"):
        return "Sell"
    if sell_risk >= config.SELL_RISK_THRESHOLDS["sell"]:
        return "Sell"

    # ── 2. Take Profit ────────────────────────────────────────────────────
    # At or above price target with ANY signs of weakening = take the gain.
    if at_profit_target and sell_risk >= 25:
        return "Take Profit"
    # Large unrealised gain + momentum stalling even without explicit target
    if unrealized_pnl_pct >= config.PROFIT_TAKE_MIN_GAIN_PCT and sell_risk >= 40:
        return "Take Profit"

    # ── 3. Trim ───────────────────────────────────────────────────────────
    # Reduce position size without fully exiting.
    if risk_factors.get("concentration_risk") and sell_risk >= 20:
        return "Trim"
    if sell_risk >= config.SELL_RISK_THRESHOLDS["trim"]:
        return "Trim"
    # Significant profit + early warning signs = take partial profits
    if unrealized_pnl_pct >= 0.12 and sell_risk >= 35:
        return "Trim"

    # ── 4. Watch Closely ─────────────────────────────────────────────────
    # Something has changed — not yet a confirmed breakdown, but caution warranted.
    if sell_risk >= config.SELL_RISK_THRESHOLDS["watch_closely"]:
        return "Watch Closely"
    if hold_quality < 45:
        return "Watch Closely"

    # ── 5. Hold ───────────────────────────────────────────────────────────
    # Trend and momentum are healthy, no major warning signs.
    return "Hold"


# ─────────────────────────────────────────────────────────────────────────────
# EXPLANATION AND ACTION ITEMS
# ─────────────────────────────────────────────────────────────────────────────

def generate_hold_explanation(
    holding: Holding,
    hold_quality: float,
    sell_risk: float,
    recommendation: str,
    factor_scores: Dict,
    risk_factors: Dict,
    daily: IndicatorData,
) -> str:
    """
    Generate a plain-English explanation of the hold/sell recommendation.

    Focuses on what changed or what the current state is — not just restating scores.
    """
    ticker = holding.ticker
    sentences = []

    # Opening verdict
    if recommendation == "Sell":
        if risk_factors.get("hard_stop_breach"):
            sentences.append(
                f"{ticker} has breached its hard stop-loss. This is a non-negotiable exit signal — "
                f"the original risk parameters for this trade have been violated."
            )
        elif risk_factors.get("trailing_stop_breach"):
            sentences.append(
                f"{ticker} has breached its trailing stop. The stock has fallen significantly "
                f"from its recent peak, triggering the profit-protection stop."
            )
        else:
            sentences.append(
                f"{ticker} shows multiple sell signals (sell risk: {sell_risk:.0f}/100). "
                f"The technical picture has deteriorated significantly."
            )

    elif recommendation == "Take Profit":
        sentences.append(
            f"{ticker} has reached or exceeded your profit target "
            f"with signs of momentum stalling. This is a good time to "
            f"take some or all of the gain off the table."
        )

    elif recommendation == "Trim":
        if risk_factors.get("concentration_risk"):
            pct = risk_factors.get("position_pct", 0)
            sentences.append(
                f"{ticker} now represents {pct:.1f}% of your portfolio — above the "
                f"{config.MAX_POSITION_PCT:.0%} concentration limit. "
                f"Reduce size to manage risk, even if the trend remains intact."
            )
        else:
            sentences.append(
                f"{ticker} shows early signs of weakening while still profitable. "
                f"Trimming reduces risk while keeping exposure if the trend recovers."
            )

    elif recommendation == "Watch Closely":
        sentences.append(
            f"{ticker} is showing early warning signs (sell risk: {sell_risk:.0f}/100, "
            f"hold quality: {hold_quality:.0f}/100). No confirmed breakdown yet, "
            f"but conditions require close monitoring."
        )

    else:  # Hold
        sentences.append(
            f"{ticker} remains in a healthy technical position "
            f"(hold quality: {hold_quality:.0f}/100, sell risk: {sell_risk:.0f}/100). "
            f"Continue holding — the trend is intact."
        )

    # Add specific factor context
    if risk_factors.get("macd_negative") and recommendation in ("Sell", "Trim", "Watch Closely"):
        sentences.append(
            "MACD histogram has turned negative — selling pressure has overtaken buying pressure."
        )

    if risk_factors.get("rsi_deteriorating") and daily.rsi is not None:
        sentences.append(
            f"RSI has dropped to {daily.rsi:.1f}, below the healthy-hold threshold of "
            f"{config.RSI_DETERIORATION_THRESHOLD:.0f}. Momentum is fading."
        )

    if risk_factors.get("below_sma20") and recommendation in ("Sell", "Trim", "Watch Closely"):
        sentences.append(
            "Price has fallen below the 20-day moving average — the short-term trend structure is broken."
        )

    drawdown = risk_factors.get("drawdown_from_peak_pct")
    if drawdown and drawdown > 8:
        sentences.append(
            f"The stock is {drawdown:.1f}% below its recent peak — "
            f"a meaningful drawdown that increases the urgency of the stop-loss."
        )

    if risk_factors.get("mtf_early_warning"):
        sentences.append(
            "The 4-hour chart's MACD has turned negative while the daily chart is still positive — "
            "an early multi-timeframe warning signal."
        )

    return " ".join(sentences)


def generate_action_items(
    recommendation: str,
    risk_factors: Dict,
    hold_quality: float,
    sell_risk: float,
    current_close: float,
    hard_stop: Optional[float],
    trailing_stop: Optional[float],
    take_profit: Optional[float],
    unrealized_pnl_pct: float,
) -> List[str]:
    """
    Generate a short list of specific, actionable next steps.
    Maximum 4 items — more than that becomes noise.
    """
    items = []

    if recommendation == "Sell":
        items.append("Exit the position at market open or current market price.")
        if hard_stop and current_close < hard_stop:
            items.append(f"Hard stop at ${hard_stop:.2f} has been violated — do not hold hoping for recovery.")
        elif trailing_stop and current_close < trailing_stop:
            items.append(f"Trailing stop at ${trailing_stop:.2f} has been violated — your locked-in profit is at risk.")

    elif recommendation == "Take Profit":
        items.append("Consider selling 50-100% of the position near the current price.")
        if take_profit:
            items.append(f"Your take-profit target was ${take_profit:.2f}. Price is at or near this level.")
        items.append(f"Unrealised gain is {unrealized_pnl_pct:.1%}. Locking in gains here is disciplined, not greedy.")

    elif recommendation == "Trim":
        items.append("Sell 25-50% of your shares to reduce position size and lock in partial profits.")
        if risk_factors.get("concentration_risk"):
            items.append(f"Reduce allocation to below {config.MAX_POSITION_PCT:.0%} of portfolio to manage concentration risk.")
        if trailing_stop:
            items.append(f"Raise your mental trailing stop — current trailing stop is at ${trailing_stop:.2f}.")

    elif recommendation == "Watch Closely":
        items.append("Review this position daily — a decision point is approaching.")
        if trailing_stop:
            items.append(f"Know your exit point: trailing stop is at ${trailing_stop:.2f}. Honour it if triggered.")
        if risk_factors.get("mtf_early_warning"):
            items.append("Watch the 4-hour chart for a confirmed MACD cross — that would be an early exit signal.")
        if risk_factors.get("rsi_deteriorating"):
            items.append("RSI is declining. If it falls below 40, treat that as a confirmation to exit.")

    else:  # Hold
        if trailing_stop:
            items.append(f"Trailing stop is at ${trailing_stop:.2f}. No action needed unless price reaches this level.")
        if take_profit and current_close < take_profit:
            gap = ((take_profit / current_close) - 1) * 100
            items.append(f"Price target is ${take_profit:.2f} — currently {gap:.1f}% away. Let the trade work.")
        items.append("No action required. Continue monitoring weekly.")

    return items[:4]  # cap at 4 items


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _compute_hard_stop(holding: Holding, daily: IndicatorData) -> Optional[float]:
    """
    Determine the hard stop price for a holding.

    Uses the user-set stop_loss if provided. Otherwise calculates an
    ATR-based stop from the avg_cost (wider than new-entry stops,
    since the position may have been held for a while).
    """
    if holding.stop_loss is not None:
        return holding.stop_loss

    if daily.atr is not None:
        return holding.avg_cost - (config.HOLD_STOP_ATR_MULTIPLIER * daily.atr)

    return None
