"""
portfolio_monitor.py — Orchestration layer for portfolio monitoring.

This module ties together all the components needed to evaluate existing holdings:
    1. Fetch current indicator data for each holding (via taapi_client)
    2. Parse and enrich the data (via indicators)
    3. Compute Hold Quality Score and Sell Risk Score (via hold_scoring)
    4. Generate recommendation, explanation, and action items
    5. Update peak prices (ratchet up trailing stops)
    6. Return structured HoldingReport objects

Usage:
    from portfolio import load_portfolio
    from portfolio_monitor import monitor_portfolio

    portfolio = load_portfolio("portfolio.json")
    reports = monitor_portfolio(portfolio)

    for report in reports:
        print(f"{report.holding.ticker}: {report.recommendation} (sell risk: {report.sell_risk_score})")
"""

import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import config
from portfolio import Holding, Portfolio, save_portfolio
from taapi_client import fetch_indicators
from indicators import parse_indicators, IndicatorData
from hold_scoring import (
    compute_hold_quality_score,
    compute_sell_risk_score,
    get_hold_recommendation,
    generate_hold_explanation,
    generate_action_items,
    _compute_hard_stop,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT STRUCTURE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class HoldingReport:
    """
    Complete analysis result for one portfolio holding.

    Includes everything needed to display in the UI or export to JSON:
        • Current market data (price, P&L)
        • Stop levels (hard stop, trailing stop)
        • Both scores (hold quality + sell risk)
        • Recommendation + explanation + action items
        • Raw indicator values for detail view
        • Factor scores breakdown
    """
    holding:          Holding           # Original holding data

    # Current market snapshot
    current_price:    float
    position_value_usd: float           # shares × current price
    unrealized_pnl_usd: float           # position_value - cost_basis
    unrealized_pnl_pct: float           # pnl / cost_basis

    # Stop levels
    hard_stop_price:  Optional[float]
    hard_stop_breach: bool
    trailing_stop_price: float
    trailing_stop_breach: bool
    at_profit_target: bool

    # Drawdown
    drawdown_from_peak_pct: Optional[float]   # e.g. 0.05 = 5% below peak

    # Scores
    hold_quality_score: float          # 0-100, higher = healthier
    sell_risk_score:    float          # 0-100, higher = more reason to exit

    # Output
    recommendation:   str              # Hold / Watch Closely / Trim / Sell / Take Profit
    hold_factor_scores: Dict[str, Any]
    sell_risk_factors:  Dict[str, Any]
    indicators:       Dict[str, Any]   # raw indicator values for display

    explanation:      str
    action_items:     List[str]

    # Metadata
    analysed_at:      str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    error:            Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# SINGLE HOLDING ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def monitor_holding(
    holding:            Holding,
    portfolio_value:    float,
    primary_interval:   str = None,
    secondary_interval: str = None,
    update_peak:        bool = True,
) -> HoldingReport:
    """
    Run the full analysis pipeline for one portfolio holding.

    Any exception is caught — one holding failing should never stop the others.
    An error report is returned instead of raising.

    Args:
        holding:            The holding to analyse
        portfolio_value:    Total current portfolio value (for concentration check)
        primary_interval:   Primary timeframe (default: config.INTERVALS["primary"])
        secondary_interval: Secondary timeframe (default: config.INTERVALS["secondary"])
        update_peak:        Whether to ratchet up the peak_price if a new high is seen

    Returns:
        HoldingReport — always returned, never raises.
    """
    primary   = primary_interval   or config.INTERVALS["primary"]
    secondary = secondary_interval or config.INTERVALS["secondary"]
    ticker    = holding.ticker

    logger.info("Monitoring %s ...", ticker)

    try:
        # ── Step 1: Fetch indicator data ──────────────────────────────────
        daily_raw = fetch_indicators(ticker, primary)

        # Fast-fail if the plan doesn't cover US stocks
        if "plan_restriction" in daily_raw.fetch_errors:
            raise RuntimeError(
                "Your TAAPI plan does not support US stocks. "
                "The free tier only covers crypto. "
                "Upgrade at https://taapi.io/pricing/ or enable Mock Mode."
            )

        h4_raw = fetch_indicators(ticker, secondary)

        daily = parse_indicators(daily_raw)
        h4    = parse_indicators(h4_raw)

        # ── Step 2: Get current price ─────────────────────────────────────
        # Use the daily close; fall back to avg_cost if unavailable
        current_price = daily.close or holding.avg_cost
        logger.debug("%s current price: %.2f", ticker, current_price)

        # ── Step 3: Update peak price (ratchet up trailing stop) ──────────
        if update_peak:
            updated = holding.update_peak(current_price)
            if updated:
                logger.debug("%s: new peak price = %.2f", ticker, holding.peak_price)

        # ── Step 4: Compute P&L ───────────────────────────────────────────
        cost_basis        = holding.shares * holding.avg_cost
        position_value    = holding.shares * current_price
        unrealized_pnl    = position_value - cost_basis
        unrealized_pnl_pct = unrealized_pnl / cost_basis if cost_basis > 0 else 0.0

        # ── Step 5: Stop levels ────────────────────────────────────────────
        hard_stop        = _compute_hard_stop(holding, daily)
        hard_stop_breach = hard_stop is not None and current_price < hard_stop

        trailing_stop       = holding.trailing_stop_price(current_price)
        trailing_stop_breach = current_price < trailing_stop

        at_profit_target = (
            holding.take_profit is not None
            and current_price >= holding.take_profit
        )

        # ── Step 6: Drawdown ──────────────────────────────────────────────
        drawdown = holding.drawdown_from_peak(current_price)

        # ── Step 7: Hold Quality Score ────────────────────────────────────
        hold_quality, hold_factors = compute_hold_quality_score(daily, h4)

        # ── Step 8: Sell Risk Score ───────────────────────────────────────
        sell_risk, risk_factors = compute_sell_risk_score(
            holding=holding,
            daily=daily,
            h4=h4,
            current_close=current_price,
            portfolio_value=portfolio_value,
        )

        # ── Step 9: Recommendation ────────────────────────────────────────
        recommendation = get_hold_recommendation(
            hold_quality=hold_quality,
            sell_risk=sell_risk,
            unrealized_pnl_pct=unrealized_pnl_pct,
            risk_factors=risk_factors,
            at_profit_target=at_profit_target,
        )

        # ── Step 10: Explanation and Action Items ─────────────────────────
        explanation = generate_hold_explanation(
            holding=holding,
            hold_quality=hold_quality,
            sell_risk=sell_risk,
            recommendation=recommendation,
            factor_scores=hold_factors,
            risk_factors=risk_factors,
            daily=daily,
        )

        action_items = generate_action_items(
            recommendation=recommendation,
            risk_factors=risk_factors,
            hold_quality=hold_quality,
            sell_risk=sell_risk,
            current_close=current_price,
            hard_stop=hard_stop,
            trailing_stop=trailing_stop,
            take_profit=holding.take_profit,
            unrealized_pnl_pct=unrealized_pnl_pct,
        )

        # ── Step 11: Serialize raw indicators ────────────────────────────
        indicators = {
            "rsi":    daily.rsi,
            "macd":   daily.macd,
            "adx":    daily.adx,
            "atr":    daily.atr,
            "atr_pct": round(daily.atr_pct * 100, 2) if daily.atr_pct else None,
            "sma_20": daily.sma_20,
            "ema_20": daily.ema_20,
            "bbands": daily.bbands,
            "stoch":  daily.stoch,
            "obv":    daily.obv,
            "close":  daily.close,
            "volume": daily.volume,
        }

        logger.info(
            "%s | %s | HQ=%.1f SR=%.1f | P&L=%.1f%%",
            ticker, recommendation, hold_quality, sell_risk,
            unrealized_pnl_pct * 100,
        )

        return HoldingReport(
            holding=holding,
            current_price=round(current_price, 2),
            position_value_usd=round(position_value, 2),
            unrealized_pnl_usd=round(unrealized_pnl, 2),
            unrealized_pnl_pct=round(unrealized_pnl_pct, 4),
            hard_stop_price=round(hard_stop, 2) if hard_stop else None,
            hard_stop_breach=hard_stop_breach,
            trailing_stop_price=round(trailing_stop, 2),
            trailing_stop_breach=trailing_stop_breach,
            at_profit_target=at_profit_target,
            drawdown_from_peak_pct=round(drawdown, 4) if drawdown is not None else None,
            hold_quality_score=hold_quality,
            sell_risk_score=sell_risk,
            recommendation=recommendation,
            hold_factor_scores=hold_factors,
            sell_risk_factors=risk_factors,
            indicators=indicators,
            explanation=explanation,
            action_items=action_items,
        )

    except Exception as exc:
        logger.error(
            "Unexpected error monitoring %s: %s\n%s",
            ticker, exc, traceback.format_exc(),
        )
        return HoldingReport(
            holding=holding,
            current_price=holding.avg_cost,
            position_value_usd=holding.cost_basis_total,
            unrealized_pnl_usd=0.0,
            unrealized_pnl_pct=0.0,
            hard_stop_price=None,
            hard_stop_breach=False,
            trailing_stop_price=holding.avg_cost * (1 - holding.trailing_stop_pct),
            trailing_stop_breach=False,
            at_profit_target=False,
            drawdown_from_peak_pct=None,
            hold_quality_score=50.0,
            sell_risk_score=50.0,
            recommendation="Watch Closely",
            hold_factor_scores={},
            sell_risk_factors={},
            indicators={},
            explanation=f"Analysis failed: {exc}",
            action_items=["Unable to analyse — check API connection or try mock mode."],
            error=str(exc),
        )


# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO-LEVEL MONITOR
# ─────────────────────────────────────────────────────────────────────────────

def monitor_portfolio(
    portfolio:          Portfolio,
    primary_interval:   Optional[str] = None,
    secondary_interval: Optional[str] = None,
    update_peaks:       bool = True,
) -> List[HoldingReport]:
    """
    Monitor all holdings in a portfolio.

    Runs monitor_holding() for each holding, then sorts results so the most
    urgent recommendations appear first (Sell before Trim before Watch, etc.).

    Peak prices are updated in-place on the Holding objects. To persist these
    updates, call save_portfolio() on the portfolio after this function returns.

    Args:
        portfolio:          Portfolio to monitor
        primary_interval:   Primary timeframe (default from config)
        secondary_interval: Secondary timeframe (default from config)
        update_peaks:       Whether to ratchet up peak_price as new highs are seen

    Returns:
        List of HoldingReport, sorted by urgency (Sell first, Hold last).
    """
    if not portfolio.holdings:
        logger.warning("Portfolio is empty — nothing to monitor")
        return []

    logger.info(
        "Starting portfolio monitor: %d holdings, mode=%s",
        len(portfolio.holdings),
        "MOCK" if config.MOCK_MODE else "LIVE",
    )

    # Compute approximate total portfolio value for concentration checks
    # We don't have live prices yet, so use avg_cost as an estimate first.
    # This is refined in the second pass below.
    estimated_value = portfolio.account_size  # use account size as baseline

    reports: List[HoldingReport] = []
    for holding in portfolio.holdings:
        report = monitor_holding(
            holding=holding,
            portfolio_value=estimated_value,
            primary_interval=primary_interval,
            secondary_interval=secondary_interval,
            update_peak=update_peaks,
        )
        reports.append(report)

    # Re-compute actual portfolio value from live prices and re-check concentration
    actual_portfolio_value = sum(
        r.position_value_usd for r in reports if r.error is None
    ) or estimated_value

    # Re-run concentration check with accurate portfolio value
    for report in reports:
        if report.error is None and actual_portfolio_value > 0:
            position_pct = report.position_value_usd / actual_portfolio_value
            report.sell_risk_factors["position_pct"] = round(position_pct * 100, 1)

            concentration = position_pct > config.MAX_POSITION_PCT
            was_concentration = report.sell_risk_factors.get("concentration_risk", False)

            if concentration != was_concentration:
                report.sell_risk_factors["concentration_risk"] = concentration
                # Recalculate sell risk to reflect updated concentration
                penalty = config.SELL_RISK_PENALTIES["concentration_risk"]
                if concentration and not was_concentration:
                    report.sell_risk_score = min(100.0, report.sell_risk_score + penalty)
                elif not concentration and was_concentration:
                    report.sell_risk_score = max(0.0, report.sell_risk_score - penalty)

                # Recalculate recommendation with updated score
                report.recommendation = get_hold_recommendation(
                    hold_quality=report.hold_quality_score,
                    sell_risk=report.sell_risk_score,
                    unrealized_pnl_pct=report.unrealized_pnl_pct,
                    risk_factors=report.sell_risk_factors,
                    at_profit_target=report.at_profit_target,
                )

    # Sort: Sell first, then Trim, Watch Closely, Hold, Take Profit
    _SORT_ORDER = {
        "Sell":          0,
        "Trim":          1,
        "Watch Closely": 2,
        "Take Profit":   3,
        "Hold":          4,
    }
    reports.sort(key=lambda r: (_SORT_ORDER.get(r.recommendation, 99), -r.sell_risk_score))

    # Log summary
    counts = {}
    for r in reports:
        counts[r.recommendation] = counts.get(r.recommendation, 0) + 1
    logger.info("Portfolio monitor complete: %s", " | ".join(f"{k}: {v}" for k, v in counts.items()))

    return reports


# ─────────────────────────────────────────────────────────────────────────────
# JSON SERIALIZATION
# ─────────────────────────────────────────────────────────────────────────────

def reports_to_json(reports: List[HoldingReport]) -> List[Dict]:
    """Convert HoldingReport objects to JSON-serializable dicts."""
    output = []
    for r in reports:
        record = {
            "ticker":              r.holding.ticker,
            "shares":              r.holding.shares,
            "avg_cost":            r.holding.avg_cost,
            "entry_date":          r.holding.entry_date,
            "current_price":       r.current_price,
            "position_value_usd":  r.position_value_usd,
            "unrealized_pnl_usd":  r.unrealized_pnl_usd,
            "unrealized_pnl_pct":  round(r.unrealized_pnl_pct * 100, 2),
            "hard_stop_price":     r.hard_stop_price,
            "hard_stop_breach":    r.hard_stop_breach,
            "trailing_stop_price": r.trailing_stop_price,
            "trailing_stop_breach": r.trailing_stop_breach,
            "drawdown_from_peak_pct": (
                round(r.drawdown_from_peak_pct * 100, 1)
                if r.drawdown_from_peak_pct is not None else None
            ),
            "hold_quality_score": r.hold_quality_score,
            "sell_risk_score":    r.sell_risk_score,
            "recommendation":     r.recommendation,
            "hold_factor_scores": r.hold_factor_scores,
            "sell_risk_factors":  {
                k: v for k, v in r.sell_risk_factors.items()
                if isinstance(v, bool) and v  # only show triggered flags
            },
            "indicators":   r.indicators,
            "explanation":  r.explanation,
            "action_items": r.action_items,
            "analysed_at":  r.analysed_at,
        }
        if r.error:
            record["error"] = r.error
        output.append(record)
    return output
