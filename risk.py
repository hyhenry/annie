"""
risk.py — Professional risk management for every trade recommendation.

This module answers the three questions every disciplined trader asks BEFORE
entering a trade:
    1. WHERE do I enter?        → entry price
    2. WHERE do I exit if wrong? → stop-loss (limits your downside)
    3. WHERE do I exit if right? → take-profit target (locks in your upside)

And the fourth question that most beginners forget:
    4. HOW MUCH do I buy?       → position size (based on how much you're willing to lose)

─────────────────────────────────────────────────────────────────────────────
WHY ATR-BASED STOPS?

Using a fixed percentage (like "always stop 2% below entry") ignores the
reality that different stocks have different volatility levels. A $200 stock
with ATR = $2 moves very differently from one with ATR = $8.

ATR-based stops say: "Place my stop far enough below entry that normal daily
noise won't shake me out, but close enough that my actual loss is controlled."

Standard practice: Stop = Entry − (1.5 × ATR)
This gives the stock 1.5 days worth of typical movement as breathing room.
─────────────────────────────────────────────────────────────────────────────
WHY 2.5:1 REWARD-TO-RISK?

If you risk $100 per trade but target $250 profit, you only need to be right
40% of the time to break even. This is the math of professional trading:
you don't need to win every trade — you just need to win enough to make the
winners cover the losers.

Rule of thumb: Never take a trade where potential profit < 2× the risk.
Default: Risk/Reward = 1:2.5 (risk $1 to potentially make $2.50).
─────────────────────────────────────────────────────────────────────────────
"""

import logging
import math
from dataclasses import dataclass
from typing import Optional

import config
from indicators import IndicatorData

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TradePlan:
    """
    A complete, actionable trade plan for one stock.

    All values are in USD. Position size is in whole shares (you can't buy
    a fraction of a share on most brokers).
    """
    # Price at which to enter the trade (current market price)
    entry: float

    # Price at which to exit if the trade goes against you (maximum loss point)
    # Formula: entry - (ATR × stop_multiplier)
    stop_loss: float

    # Price at which to exit if the trade goes in your favour (profit target)
    # Formula: entry + (stop_distance × reward_risk_ratio)
    take_profit: float

    # How many shares to buy so that hitting stop_loss = risk_per_trade_usd
    position_size_shares: int

    # Actual dollar amount at risk (may differ slightly from target due to rounding)
    risk_amount_usd: float

    # How many dollars profit you stand to make if take_profit is hit
    reward_amount_usd: float

    # Ratio of potential reward to risk (e.g. 2.5 means "make $2.50 per $1 risked")
    reward_risk_ratio: float

    # Plain-English risk summary for this specific trade
    risk_note: str


# ─────────────────────────────────────────────────────────────────────────────
# STOP-LOSS CALCULATION
# ─────────────────────────────────────────────────────────────────────────────

def compute_stop_loss(
    close: float,
    atr: float,
    atr_multiplier: Optional[float] = None,
) -> float:
    """
    Calculate an ATR-based stop-loss price.

    Formula: stop_loss = close − (atr × multiplier)

    The multiplier (default 1.5) provides a buffer equal to 1.5 average
    daily price ranges below the entry. This is enough room that a normal
    "bad day" won't stop you out, but a genuine reversal will.

    Plain English example:
        Stock price: $186.50
        ATR (average daily range): $3.40
        Stop-loss = $186.50 - ($3.40 × 1.5) = $186.50 - $5.10 = $181.40

        This means: "If this stock drops $5.10 from my entry, I'm out."

    Args:
        close:          Current stock price (entry price)
        atr:            Average True Range (daily price movement)
        atr_multiplier: How many ATRs to use as buffer (default from config)

    Returns:
        Stop-loss price, rounded to 2 decimal places.
    """
    multiplier = atr_multiplier if atr_multiplier is not None else config.ATR_STOP_LOSS_MULTIPLIER
    stop = close - (atr * multiplier)
    return round(max(0.01, stop), 2)  # stop can't be negative


def compute_take_profit(
    close: float,
    stop_loss: float,
    reward_risk_ratio: Optional[float] = None,
) -> float:
    """
    Calculate a take-profit target using a fixed reward-to-risk ratio.

    Formula: take_profit = close + (stop_distance × reward_risk_ratio)

    The default ratio of 2.5 means: for every $1 you risk, you target $2.50 profit.
    This gives you a favourable "expectancy" — even winning only 40% of trades
    would be profitable over time.

    Plain English example:
        Entry:     $186.50
        Stop-loss: $181.40  → stop distance = $5.10
        Take-profit = $186.50 + ($5.10 × 2.5) = $186.50 + $12.75 = $199.25

        This means: "If this trade works out, I plan to sell near $199.25."

    Args:
        close:             Current stock price (entry price)
        stop_loss:         Stop-loss price (calculated above)
        reward_risk_ratio: Target profit multiple of risk (default from config)

    Returns:
        Take-profit price, rounded to 2 decimal places.
    """
    ratio = reward_risk_ratio if reward_risk_ratio is not None else config.ATR_TAKE_PROFIT_MULTIPLIER
    stop_distance = close - stop_loss
    take_profit   = close + (stop_distance * ratio)
    return round(take_profit, 2)


# ─────────────────────────────────────────────────────────────────────────────
# POSITION SIZE CALCULATION
# ─────────────────────────────────────────────────────────────────────────────

def compute_position_size(
    close: float,
    stop_loss: float,
    risk_per_trade_usd: Optional[float] = None,
) -> tuple:
    """
    Calculate how many shares to buy so that your total loss, if stopped out,
    equals exactly your target risk budget.

    Formula:
        risk_per_share = close - stop_loss  (your loss per share if stopped out)
        shares = floor(risk_budget / risk_per_share)

    Plain English example:
        Entry:         $186.50
        Stop-loss:     $181.40
        Risk/share:    $5.10
        Risk budget:   $500
        Shares:        floor($500 / $5.10) = 98 shares
        Actual risk:   98 × $5.10 = $499.80

    Why floor() not round()? Because rounding UP would exceed your risk budget.
    The 98-share trade risks exactly $499.80 — just under $500.

    Args:
        close:              Entry price
        stop_loss:          Stop-loss price
        risk_per_trade_usd: Total dollar risk budget (default from config)

    Returns:
        Tuple of (shares: int, actual_risk_usd: float)
    """
    budget = risk_per_trade_usd if risk_per_trade_usd is not None else config.RISK_PER_TRADE_USD
    risk_per_share = close - stop_loss

    if not (risk_per_share > 0):   # catches NaN, zero, and negative
        logger.warning(
            "Cannot compute position size: risk_per_share=%s (close=%.4g, stop_loss=%.4g)",
            risk_per_share, close, stop_loss,
        )
        return 0, 0.0

    shares = math.floor(budget / risk_per_share)
    shares = max(0, shares)
    actual_risk = round(shares * risk_per_share, 2)

    return shares, actual_risk


# ─────────────────────────────────────────────────────────────────────────────
# RISK NOTE GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def _generate_risk_note(
    data: IndicatorData,
    entry: float,
    stop_loss: float,
    take_profit: float,
    reward_risk_ratio: float,
    score: float,
) -> str:
    """
    Generate a plain-English risk note tailored to this specific trade setup.

    Covers:
        • Why the stop is placed where it is
        • The reward-to-risk ratio in plain terms
        • Any specific caution flags for this stock
    """
    parts = []

    # Stop explanation
    atr_val = data.atr
    stop_dist = round(entry - stop_loss, 2)
    if atr_val:
        atr_pct = round((data.atr_pct or 0) * 100, 1)
        parts.append(
            f"Your stop-loss is ${stop_dist:.2f} below entry, equal to "
            f"1.5× the stock's average daily range (ATR = ${atr_val:.2f}, "
            f"which is {atr_pct}% of the price). "
            f"This gives the stock room to breathe on a normal day without stopping you out."
        )
    else:
        parts.append(
            f"Your stop-loss is ${stop_dist:.2f} below entry."
        )

    # Reward-to-risk
    potential_gain = round(take_profit - entry, 2)
    parts.append(
        f"If the trade works, you stand to gain ${potential_gain:.2f} per share "
        f"(a {reward_risk_ratio:.1f}:1 reward-to-risk ratio — you risk $1 to potentially make ${reward_risk_ratio:.1f})."
    )

    # Volatility caution
    if data.atr_pct is not None:
        if data.atr_pct > 0.04:
            parts.append(
                "Caution: volatility is elevated. Larger daily swings mean "
                "you may need to size your position smaller to keep risk manageable."
            )
        elif data.atr_pct <= 0.015:
            parts.append(
                "Volatility is low, which makes stop-loss placement more precise "
                "and position sizing easier."
            )

    # RSI caution
    if data.rsi is not None:
        if data.rsi > config.RSI_OVERBOUGHT:
            parts.append(
                f"Warning: RSI is {data.rsi:.1f}, above the overbought threshold of "
                f"{config.RSI_OVERBOUGHT:.0f}. "
                f"Consider waiting for a small pullback before entering."
            )
        elif data.rsi < config.RSI_OVERSOLD:
            parts.append(
                f"Warning: RSI is {data.rsi:.1f}, in oversold territory. "
                f"Price may still fall further before recovering."
            )

    # Score context
    if score >= 80:
        parts.append(
            "Overall signal quality is strong — this is a relatively high-confidence setup."
        )
    elif score < 60:
        parts.append(
            "This setup has significant uncertainties. If you do trade it, "
            "keep position size small."
        )

    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# MASTER FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def build_trade_plan(
    data: IndicatorData,
    score: float,
    filters_passed: bool,
) -> Optional[TradePlan]:
    """
    Build a complete trade plan for a stock.

    Returns None if:
        • Hard filters failed (stock is "Avoid" — no trade plan needed)
        • Close price is missing (can't calculate anything without a price)
        • ATR is missing (can't size the stop-loss)

    Args:
        data:           Parsed indicator data for the stock
        score:          Final score (0-100) from scoring.py
        filters_passed: Whether the stock passed hard filters

    Returns:
        TradePlan with entry, stop-loss, take-profit, and position size,
        or None if a plan cannot be generated.
    """
    # Can't make a trade plan for an "Avoid"
    if not filters_passed:
        return None

    # Need a price to calculate anything
    if data.close is None:
        logger.warning("Cannot build trade plan for %s: close price missing", data.ticker)
        return None

    # Need ATR for stop placement — if missing, use a 2% fallback
    if data.atr is None:
        logger.warning(
            "ATR missing for %s — using 2%% of price as fallback stop distance",
            data.ticker
        )
        atr_fallback = data.close * 0.02
        atr_for_plan = atr_fallback
    else:
        atr_for_plan = data.atr

    entry      = data.close
    stop_loss  = compute_stop_loss(entry, atr_for_plan)
    take_profit = compute_take_profit(entry, stop_loss)
    shares, actual_risk = compute_position_size(entry, stop_loss)

    # Compute actual reward amount
    reward_per_share  = take_profit - entry
    reward_amount_usd = round(shares * reward_per_share, 2)
    stop_distance     = entry - stop_loss
    rr_ratio          = round(reward_per_share / stop_distance, 2) if stop_distance > 0 else 0.0

    risk_note = _generate_risk_note(
        data, entry, stop_loss, take_profit, rr_ratio, score
    )

    plan = TradePlan(
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        position_size_shares=shares,
        risk_amount_usd=actual_risk,
        reward_amount_usd=reward_amount_usd,
        reward_risk_ratio=rr_ratio,
        risk_note=risk_note,
    )

    logger.debug(
        "%s trade plan: entry=%.2f stop=%.2f target=%.2f shares=%d risk=$%.2f",
        data.ticker, entry, stop_loss, take_profit, shares, actual_risk
    )

    return plan
