"""
engine.py — The orchestration layer.

This module coordinates the full analysis pipeline for a list of stock tickers.
It calls the other modules in the correct sequence, handles per-ticker errors
gracefully, sorts results by score, and converts everything into the final
JSON-serializable output format.

Pipeline per ticker:
    1. Fetch raw indicators (daily timeframe)  → taapi_client
    2. Fetch raw indicators (4h timeframe)     → taapi_client
    3. Parse and enrich indicator data         → indicators
    4. Score the stock                         → scoring
    5. Build trade plan                        → risk
    6. Assemble StockReport
    7. Repeat for all tickers, then sort by score
"""

import json
import logging
import traceback
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional

import config
from taapi_client import fetch_indicators
from indicators import parse_indicators, IndicatorData
from scoring import score_ticker, ScoringResult
from risk import build_trade_plan, TradePlan

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StockReport:
    """
    Final output record for one stock.
    Matches the JSON output schema specified in the project requirements.
    """
    ticker:          str
    score:           float
    recommendation:  str           # "Buy", "Watch", or "Avoid"
    confidence:      float
    factor_scores:   Dict[str, Any]
    indicators:      Dict[str, Any]
    trade_plan:      Optional[Dict[str, Any]]
    risk_note:       str
    explanation:     str
    filters_passed:  bool
    filter_failures: List[str] = field(default_factory=list)
    error:           Optional[str] = None  # Non-None if analysis failed entirely


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR SERIALIZER
# ─────────────────────────────────────────────────────────────────────────────

def _serialize_indicators(daily: IndicatorData) -> Dict[str, Any]:
    """
    Convert IndicatorData into the JSON output format.

    Maps internal field names to the clean output schema.
    All None values are preserved (consumer can check for missing data).
    """
    return {
        "rsi":     daily.rsi,
        "macd":    daily.macd,     # dict with value/signal/histogram or None
        "adx":     daily.adx,
        "atr":     daily.atr,
        "atr_pct": round(daily.atr_pct * 100, 2) if daily.atr_pct is not None else None,  # as %
        "sma_20":  daily.sma_20,
        "ema_20":  daily.ema_20,
        "bbands":  daily.bbands,   # dict with upper/middle/lower or None
        "stoch":   daily.stoch,    # dict with k/d or None
        "obv":     daily.obv,
        "close":   daily.close,
        "volume":  daily.volume,
    }


# ─────────────────────────────────────────────────────────────────────────────
# TRADE PLAN SERIALIZER
# ─────────────────────────────────────────────────────────────────────────────

def _serialize_trade_plan(plan: Optional[TradePlan]) -> Optional[Dict[str, Any]]:
    """Convert TradePlan to JSON-serializable dict, or None if no plan available."""
    if plan is None:
        return None
    return {
        "entry":                plan.entry,
        "stop_loss":            plan.stop_loss,
        "take_profit":          plan.take_profit,
        "position_size_shares": plan.position_size_shares,
        "risk_amount_usd":      plan.risk_amount_usd,
        "reward_amount_usd":    plan.reward_amount_usd,
        "reward_risk_ratio":    plan.reward_risk_ratio,
    }


# ─────────────────────────────────────────────────────────────────────────────
# FACTOR SCORES SERIALIZER
# ─────────────────────────────────────────────────────────────────────────────

def _serialize_factor_scores(result: ScoringResult) -> Dict[str, Any]:
    """Convert FactorScores to dict. Risk penalty is shown as a negative (deduction)."""
    fs = result.factor_scores
    deduction = round(fs.risk_penalty * config.FACTOR_WEIGHTS["risk_penalty"], 1)
    return {
        "trend":           fs.trend,
        "momentum":        fs.momentum,
        "volume":          fs.volume,
        "entry_timing":    fs.entry_timing,
        "volatility":      fs.volatility,
        "multi_timeframe": fs.multi_timeframe,
        "risk_penalty":    -deduction,  # display as negative to show it reduces the score
    }


# ─────────────────────────────────────────────────────────────────────────────
# SINGLE TICKER ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def analyze_ticker(
    ticker: str,
    primary_interval: str = None,
    secondary_interval: str = None,
) -> StockReport:
    """
    Run the full analysis pipeline for one stock ticker.

    Steps:
        1. Fetch indicators at primary timeframe (daily by default)
        2. Fetch indicators at secondary timeframe (4h by default)
        3. Parse and enrich both bundles
        4. Score the stock
        5. Build trade plan
        6. Assemble and return StockReport

    Any exception at any step is caught. If the ticker fails completely,
    an error report is returned instead of crashing the entire run.

    Args:
        ticker:             Stock symbol, e.g. "AAPL"
        primary_interval:   Primary timeframe (default from config)
        secondary_interval: Secondary timeframe (default from config)

    Returns:
        StockReport — always returned, never raises.
    """
    primary   = primary_interval   or config.INTERVALS["primary"]
    secondary = secondary_interval or config.INTERVALS["secondary"]

    ticker = ticker.upper().strip()
    logger.info("Analysing %s ...", ticker)

    try:
        # ── Step 1: Fetch primary timeframe data ──────────────────────────
        logger.debug("%s: fetching %s indicators", ticker, primary)
        daily_raw = fetch_indicators(ticker, primary)

        # ── Step 2: Fetch secondary timeframe data ────────────────────────
        logger.debug("%s: fetching %s indicators", ticker, secondary)
        h4_raw = fetch_indicators(ticker, secondary)

        # ── Step 3: Parse and enrich ──────────────────────────────────────
        daily = parse_indicators(daily_raw)
        h4    = parse_indicators(h4_raw)

        # ── Step 4: Score ─────────────────────────────────────────────────
        result = score_ticker(ticker, daily, h4)

        # ── Step 5: Trade plan ────────────────────────────────────────────
        plan = build_trade_plan(daily, result.final_score, result.filters_passed)

        # ── Step 6: Assemble report ───────────────────────────────────────
        report = StockReport(
            ticker=ticker,
            score=result.final_score,
            recommendation=result.recommendation,
            confidence=result.confidence,
            factor_scores=_serialize_factor_scores(result),
            indicators=_serialize_indicators(daily),
            trade_plan=_serialize_trade_plan(plan),
            risk_note=plan.risk_note if plan else (
                "No trade plan generated — stock did not pass filters or data was insufficient."
            ),
            explanation=result.explanation,
            filters_passed=result.filters_passed,
            filter_failures=result.filter_failures,
        )

        return report

    except Exception as exc:
        # One ticker failing should NOT stop the others from being analysed.
        # Log the full traceback for debugging, return an error report.
        logger.error(
            "Unexpected error analysing %s: %s\n%s",
            ticker, exc, traceback.format_exc()
        )
        return StockReport(
            ticker=ticker,
            score=0.0,
            recommendation="Avoid",
            confidence=0.0,
            factor_scores={},
            indicators={},
            trade_plan=None,
            risk_note="",
            explanation=f"Analysis failed due to an internal error: {exc}",
            filters_passed=False,
            filter_failures=["analysis_error"],
            error=str(exc),
        )


# ─────────────────────────────────────────────────────────────────────────────
# MULTI-TICKER ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def run_engine(
    tickers: List[str],
    primary_interval: Optional[str] = None,
    secondary_interval: Optional[str] = None,
    output_file: Optional[str] = None,
    verbose: bool = False,
) -> List[StockReport]:
    """
    Run the full recommendation engine for a list of stock tickers.

    Analyses each ticker, sorts results by score (highest first), and
    optionally writes the output JSON to a file.

    Args:
        tickers:            List of stock symbols to analyse
        primary_interval:   Primary timeframe (default: daily from config)
        secondary_interval: Secondary timeframe (default: 4h from config)
        output_file:        Path to write JSON output (None = no file output)
        verbose:            Print per-ticker progress to logger at INFO level

    Returns:
        List of StockReport sorted by score descending (best opportunities first).
    """
    if not tickers:
        logger.warning("No tickers provided — nothing to analyse")
        return []

    logger.info(
        "Starting analysis of %d ticker(s): %s",
        len(tickers), ", ".join(tickers[:10]) + ("..." if len(tickers) > 10 else "")
    )
    logger.info("Mode: %s", "MOCK" if config.MOCK_MODE else "LIVE API")
    logger.info(
        "Timeframes: primary=%s, secondary=%s",
        primary_interval or config.INTERVALS["primary"],
        secondary_interval or config.INTERVALS["secondary"],
    )

    reports: List[StockReport] = []

    for i, ticker in enumerate(tickers, 1):
        if verbose:
            logger.info("[%d/%d] Processing %s ...", i, len(tickers), ticker)

        report = analyze_ticker(ticker, primary_interval, secondary_interval)
        reports.append(report)

        if verbose:
            _log_report_summary(report)

    # Sort by score descending — best opportunities first
    # Tie-break: confidence descending (more certain results ranked higher)
    reports.sort(key=lambda r: (r.score, r.confidence), reverse=True)

    logger.info(
        "Analysis complete. %d Buy | %d Watch | %d Avoid",
        sum(1 for r in reports if r.recommendation == "Buy"),
        sum(1 for r in reports if r.recommendation == "Watch"),
        sum(1 for r in reports if r.recommendation == "Avoid"),
    )

    # Write to file if requested
    if output_file:
        output_data = reports_to_json(reports)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, default=str)
        logger.info("Results written to %s", output_file)

    return reports


def _log_report_summary(report: StockReport) -> None:
    """Print a single-line summary of a report to the logger."""
    icon = {"Buy": "✓", "Watch": "~", "Avoid": "✗"}.get(report.recommendation, "?")
    logger.info(
        "  %s %s | Score: %.1f | %s | Confidence: %.1f",
        icon, report.ticker, report.score, report.recommendation, report.confidence
    )
    if report.filter_failures:
        logger.info("    Filters failed: %s", report.filter_failures)


# ─────────────────────────────────────────────────────────────────────────────
# JSON SERIALIZATION
# ─────────────────────────────────────────────────────────────────────────────

def reports_to_json(reports: List[StockReport]) -> List[Dict[str, Any]]:
    """
    Convert a list of StockReport objects into JSON-serializable dicts.

    The output matches the schema defined in the project requirements.
    None values are included (so consumers know which data was unavailable).
    """
    output = []
    for report in reports:
        record: Dict[str, Any] = {
            "ticker":          report.ticker,
            "score":           report.score,
            "recommendation":  report.recommendation,
            "confidence":      report.confidence,
            "factor_scores":   report.factor_scores,
            "indicators":      report.indicators,
            "trade_plan":      report.trade_plan,
            "risk_note":       report.risk_note,
            "explanation":     report.explanation,
            "filters_passed":  report.filters_passed,
            "filter_failures": report.filter_failures,
        }
        # Only include error field if there was an error (cleaner output)
        if report.error:
            record["error"] = report.error
        output.append(record)
    return output


# ─────────────────────────────────────────────────────────────────────────────
# TICKER FILE LOADER
# ─────────────────────────────────────────────────────────────────────────────

def load_tickers(path: str = "tickers.json") -> List[str]:
    """
    Load a list of stock tickers from a JSON file.

    The file should contain a JSON array of strings:
        ["AAPL", "MSFT", "NVDA"]

    Strips whitespace, converts to uppercase, and deduplicates.

    Args:
        path: Path to the JSON file (relative to working directory)

    Returns:
        List of clean, uppercase ticker symbols.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            raise ValueError("tickers.json must contain a JSON array, e.g. [\"AAPL\", \"MSFT\"]")
        tickers = list(dict.fromkeys(  # deduplicate while preserving order
            t.upper().strip() for t in raw if isinstance(t, str) and t.strip()
        ))
        logger.info("Loaded %d tickers from %s", len(tickers), path)
        return tickers
    except FileNotFoundError:
        logger.error("Ticker file not found: %s", path)
        return []
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in %s: %s", path, e)
        return []
