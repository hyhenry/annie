"""
portfolio.py — Portfolio data model and JSON persistence.

This module handles everything related to storing and loading portfolio data.
It knows nothing about scoring or indicators — it's purely about the data.

Portfolio JSON format:
    {
        "account_size": 50000,
        "holdings": [
            {
                "ticker": "AAPL",
                "shares": 100,
                "avg_cost": 170.00,
                "entry_date": "2024-06-15",
                "thesis": "AI services growth...",
                "stop_loss": null,
                "take_profit": 210.00,
                "peak_price": 195.00,
                "trailing_stop_pct": 0.08
            }
        ]
    }
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import List, Optional, Any, Dict

import config

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Holding:
    """
    Represents one position in a portfolio.

    Plain-English guide to each field:
        ticker:           The stock symbol (e.g. "AAPL")
        shares:           How many shares you own
        avg_cost:         What you paid per share on average (your cost basis)
        entry_date:       When you first entered the position (ISO date string)
        thesis:           Why you bought it — this keeps you honest when reviewing
        stop_loss:        Optional hard price floor — sell immediately if breached
        take_profit:      Optional target price — consider selling near here
        peak_price:       Highest price seen since entry — used for trailing stop
        trailing_stop_pct: Trailing stop distance as a fraction (0.08 = 8%)
    """
    ticker:              str
    shares:              float
    avg_cost:            float            # $ per share
    entry_date:          str              # ISO date, e.g. "2024-06-15"
    thesis:              str  = ""        # Optional reason for purchase
    stop_loss:           Optional[float] = None   # Hard stop-loss price
    take_profit:         Optional[float] = None   # Take-profit target price
    peak_price:          Optional[float] = None   # Highest price since entry
    trailing_stop_pct:   float = config.TRAILING_STOP_DEFAULT_PCT

    @property
    def cost_basis_total(self) -> float:
        """Total amount invested in this position."""
        return self.shares * self.avg_cost

    def trailing_stop_price(self, current_close: float) -> float:
        """
        Current trailing stop price.
        Uses peak_price if available, otherwise uses current close as reference.
        """
        reference = self.peak_price if self.peak_price else current_close
        return reference * (1.0 - self.trailing_stop_pct)

    def update_peak(self, current_price: float) -> bool:
        """
        Ratchet peak_price up if current price is a new high.
        Returns True if peak was updated.
        """
        if self.peak_price is None or current_price > self.peak_price:
            self.peak_price = round(current_price, 4)
            return True
        return False

    def drawdown_from_peak(self, current_price: float) -> Optional[float]:
        """
        How far has the price fallen from its peak?
        Returns a fraction (0.10 = 10% drawdown) or None if no peak recorded.
        """
        if not self.peak_price or self.peak_price <= 0:
            return None
        return (self.peak_price - current_price) / self.peak_price

    def days_held(self) -> int:
        """Number of days this position has been open."""
        try:
            entry = date.fromisoformat(self.entry_date)
            return (date.today() - entry).days
        except (ValueError, TypeError):
            return 0


@dataclass
class Portfolio:
    """
    A collection of holdings with a total account size.

    The account_size is used for position sizing and concentration risk checks.
    """
    holdings:     List[Holding] = field(default_factory=list)
    account_size: float = config.ACCOUNT_SIZE_USD

    def total_cost_basis(self) -> float:
        """Sum of all money invested across all holdings."""
        return sum(h.cost_basis_total for h in self.holdings)

    def get_holding(self, ticker: str) -> Optional[Holding]:
        """Find a holding by ticker symbol (case-insensitive)."""
        ticker = ticker.upper()
        for h in self.holdings:
            if h.ticker.upper() == ticker:
                return h
        return None

    def add_holding(self, holding: Holding) -> None:
        """Add a holding. Replaces existing holding for the same ticker."""
        self.holdings = [h for h in self.holdings if h.ticker.upper() != holding.ticker.upper()]
        self.holdings.append(holding)

    def remove_holding(self, ticker: str) -> bool:
        """Remove a holding by ticker. Returns True if it existed."""
        before = len(self.holdings)
        self.holdings = [h for h in self.holdings if h.ticker.upper() != ticker.upper()]
        return len(self.holdings) < before

    def tickers(self) -> List[str]:
        """All ticker symbols in the portfolio."""
        return [h.ticker for h in self.holdings]


# ─────────────────────────────────────────────────────────────────────────────
# JSON PERSISTENCE
# ─────────────────────────────────────────────────────────────────────────────

def _holding_to_dict(h: Holding) -> Dict[str, Any]:
    """Convert a Holding to a JSON-serializable dict."""
    return {
        "ticker":            h.ticker,
        "shares":            h.shares,
        "avg_cost":          h.avg_cost,
        "entry_date":        h.entry_date,
        "thesis":            h.thesis,
        "stop_loss":         h.stop_loss,
        "take_profit":       h.take_profit,
        "peak_price":        h.peak_price,
        "trailing_stop_pct": h.trailing_stop_pct,
    }


def _holding_from_dict(d: Dict[str, Any]) -> Holding:
    """Parse a Holding from a dict. Missing optional fields default gracefully."""
    return Holding(
        ticker=str(d.get("ticker", "")).upper().strip(),
        shares=float(d.get("shares", 0)),
        avg_cost=float(d.get("avg_cost", 0)),
        entry_date=str(d.get("entry_date", date.today().isoformat())),
        thesis=str(d.get("thesis", "")),
        stop_loss=_optional_float(d.get("stop_loss")),
        take_profit=_optional_float(d.get("take_profit")),
        peak_price=_optional_float(d.get("peak_price")),
        trailing_stop_pct=float(d.get("trailing_stop_pct", config.TRAILING_STOP_DEFAULT_PCT)),
    )


def _optional_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def load_portfolio(path: str = None) -> Portfolio:
    """
    Load a portfolio from a JSON file.

    If the file doesn't exist, returns an empty portfolio (doesn't raise).
    If the file is malformed, logs an error and returns empty portfolio.

    Args:
        path: Path to the portfolio JSON file (default: config.PORTFOLIO_FILE)

    Returns:
        Portfolio object (possibly empty if file not found or invalid)
    """
    path = path or config.PORTFOLIO_FILE

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        logger.info("Portfolio file not found at %s — starting with empty portfolio", path)
        return Portfolio()
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in portfolio file %s: %s", path, e)
        return Portfolio()

    holdings = []
    for i, raw_h in enumerate(raw.get("holdings", [])):
        try:
            h = _holding_from_dict(raw_h)
            if h.ticker and h.shares > 0 and h.avg_cost > 0:
                holdings.append(h)
            else:
                logger.warning("Skipping invalid holding at index %d: %s", i, raw_h)
        except Exception as e:
            logger.warning("Error parsing holding at index %d: %s", i, e)

    portfolio = Portfolio(
        holdings=holdings,
        account_size=float(raw.get("account_size", config.ACCOUNT_SIZE_USD)),
    )

    logger.info("Loaded portfolio: %d holdings, account size $%.0f", len(holdings), portfolio.account_size)
    return portfolio


def save_portfolio(portfolio: Portfolio, path: str = None) -> None:
    """
    Save a portfolio to a JSON file.

    Creates the file if it doesn't exist.
    Raises IOError if the file cannot be written.

    Args:
        portfolio: Portfolio object to save
        path:      Path to save to (default: config.PORTFOLIO_FILE)
    """
    path = path or config.PORTFOLIO_FILE

    data = {
        "account_size": portfolio.account_size,
        "last_updated": datetime.now().isoformat(timespec="seconds"),
        "holdings": [_holding_to_dict(h) for h in portfolio.holdings],
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    logger.debug("Portfolio saved to %s (%d holdings)", path, len(portfolio.holdings))
