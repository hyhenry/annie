"""
config.py — The single source of truth for every tunable setting.

If you want to tweak the engine's behaviour — change weights, tighten filters,
adjust risk settings — this is the ONLY file you need to edit.

No trading knowledge is required to understand this file.
Every section is labelled and explained in plain English.
"""

import os

# ─────────────────────────────────────────────────────────────────────────────
# TAAPI.io CONNECTION SETTINGS
# ─────────────────────────────────────────────────────────────────────────────
# TAAPI.io is the data provider. It serves technical indicator values for
# thousands of stocks. You need an account at taapi.io to use the live API.

TAAPI_BASE_URL: str = "https://api.taapi.io"

# Your secret API key is loaded from the .env file — never hardcode it here.
# See .env.example for the format.
TAAPI_SECRET: str = os.getenv("TAAPI_SECRET", "")

# For US stocks, the exchange identifier on TAAPI is "stocks".
TAAPI_EXCHANGE: str = os.getenv("TAAPI_EXCHANGE", "stocks")

# TAAPI expects stock symbols in the format "AAPL/USD".
TAAPI_SYMBOL_SUFFIX: str = "/USD"

# How long to wait (in seconds) before giving up on a single API call.
TAAPI_TIMEOUT_SECONDS: int = 15

# How many times to retry a failed API call before giving up.
TAAPI_MAX_RETRIES: int = 3

# Seconds to wait before the first retry, doubles each attempt (exponential backoff).
# e.g. 1st retry waits 1.5s, 2nd waits 3s, 3rd waits 6s.
TAAPI_RETRY_BACKOFF_BASE: float = 1.5

# Free-tier TAAPI accounts are rate-limited to about 1 call per second.
# This delay (in seconds) is inserted between individual API calls to stay safe.
TAAPI_RATE_LIMIT_DELAY: float = 1.5

# Set to True if you have a Pro TAAPI plan, which supports a faster "bulk"
# endpoint (fetches all indicators for one stock in a single HTTP call).
USE_BULK_API: bool = os.getenv("USE_BULK_API", "false").lower() == "true"

# ─────────────────────────────────────────────────────────────────────────────
# MOCK MODE
# ─────────────────────────────────────────────────────────────────────────────
# Set to True (or pass --mock on the CLI) to run the engine with built-in
# sample data instead of making real API calls. Useful for:
#   • Testing without an API key
#   • Development and debugging
#   • Understanding what the output looks like

MOCK_MODE: bool = os.getenv("MOCK_MODE", "false").lower() == "true"

# ─────────────────────────────────────────────────────────────────────────────
# TIMEFRAMES
# ─────────────────────────────────────────────────────────────────────────────
# The engine analyses each stock at two timeframes and rewards "agreement"
# between them. This is called multi-timeframe (MTF) confirmation.
#
# Think of it like getting a second opinion: if both the daily and 4-hour
# charts say "buy", that is more convincing than just one.
#
# TAAPI interval codes: "1d" = daily, "4h" = 4-hour, "1h" = 1-hour, "1w" = weekly

INTERVALS: dict = {
    "primary":   "1d",   # Main analysis timeframe (daily chart)
    "secondary": "4h",   # Confirmation timeframe (4-hour chart)
}

# ─────────────────────────────────────────────────────────────────────────────
# SCORING FACTOR WEIGHTS
# ─────────────────────────────────────────────────────────────────────────────
# The final score (0–100) is a weighted average of 6 factor scores, minus a
# small risk penalty.
#
# IMPORTANT: The first 6 weights must sum to 0.95. The "risk_penalty" weight
# (0.05) controls the maximum possible deduction. Together they sum to 1.0.
#
# To increase the importance of a factor, raise its weight and lower another.
# Example: if you care more about trend strength, raise "trend" to 0.30 and
# lower something else by 0.05.

FACTOR_WEIGHTS: dict = {
    "trend":           0.25,  # Is the stock in a clear uptrend?
    "momentum":        0.20,  # Is buying pressure accelerating?
    "volume":          0.15,  # Is volume confirming the move?
    "entry_timing":    0.15,  # Is this a good entry point (not chasing)?
    "volatility":      0.10,  # Is daily price movement manageable?
    "multi_timeframe": 0.10,  # Do daily + 4h charts agree?
    "risk_penalty":    0.05,  # Maximum deduction for conflicts/missing data
}

# ─────────────────────────────────────────────────────────────────────────────
# RECOMMENDATION BANDS
# ─────────────────────────────────────────────────────────────────────────────
# These score thresholds map a numeric score to an action label.
# Score of 84 → "Buy". Score of 68 → "Watch". Score of 45 → "Avoid".

RECOMMENDATION_BANDS: dict = {
    "Buy":   80,   # Score >= 80 → Buy
    "Watch": 60,   # Score 60-79 → Watch (monitor, not ready yet)
    # Score < 60   → Avoid
}

# ─────────────────────────────────────────────────────────────────────────────
# HARD FILTERS (AUTOMATIC DISQUALIFIERS)
# ─────────────────────────────────────────────────────────────────────────────
# If ANY of these filters fail, the stock is automatically labelled "Avoid"
# regardless of its score. These are non-negotiable safety guardrails.

HARD_FILTERS: dict = {
    # Ignore stocks below $5. These are "penny stocks" — too risky and
    # often thinly traded with wide spreads.
    "min_price": float(os.getenv("MIN_PRICE", "5.0")),

    # Ignore stocks with fewer than 500,000 shares traded per day on average.
    # Low-volume stocks are hard to enter and exit without moving the price.
    "min_avg_volume": int(os.getenv("MIN_AVG_VOLUME", "500000")),

    # Ignore stocks where the Average True Range (ATR) exceeds 8% of the
    # stock price. ATR measures daily price swings. At 8%+, the normal
    # daily moves are so large that reasonable stop-losses become very wide,
    # dramatically increasing risk. (ATR is explained further in indicators.py)
    "max_atr_pct": float(os.getenv("MAX_ATR_PCT", "0.08")),
}

# ─────────────────────────────────────────────────────────────────────────────
# RSI SETTINGS
# ─────────────────────────────────────────────────────────────────────────────
# RSI (Relative Strength Index) is a momentum meter that runs from 0 to 100.
#
# Plain-English meaning:
#   • RSI below 30 = the stock has sold off hard — it may be "oversold"
#   • RSI above 70 = the stock has rallied strongly — it may be "overbought"
#   • RSI 40–65   = the "sweet spot" for swing trading entries — momentum
#                   is healthy but there is still room to run
#
# Why we avoid extremes: buying at RSI 80 means you are late to the party.
# Buying at RSI 20 means you are catching a falling knife.

RSI_IDEAL_LOW:  float = 40.0   # Below this starts losing score
RSI_IDEAL_HIGH: float = 65.0   # Above this starts losing score
RSI_OVERSOLD:   float = 30.0   # Below this = strong penalty
RSI_OVERBOUGHT: float = 75.0   # Above this = strong penalty

# ─────────────────────────────────────────────────────────────────────────────
# ADX SETTINGS
# ─────────────────────────────────────────────────────────────────────────────
# ADX (Average Directional Index) measures TREND STRENGTH — not direction.
# A high ADX means a strong trend exists (up OR down). We use it alongside
# price position to confirm that an uptrend is genuine, not just noise.
#
# Plain-English meaning:
#   • ADX < 15  = no meaningful trend (price is drifting sideways)
#   • ADX 15–25 = weak-to-developing trend
#   • ADX > 25  = confirmed trend in place
#   • ADX > 40  = very strong trend (price is moving with conviction)

ADX_VERY_STRONG: float = 40.0  # Full trend-score bonus
ADX_STRONG:      float = 25.0  # Confirmed trend threshold
ADX_WEAK:        float = 15.0  # Below this = no real trend

# ─────────────────────────────────────────────────────────────────────────────
# ATR SETTINGS (VOLATILITY AND STOP-LOSS)
# ─────────────────────────────────────────────────────────────────────────────
# ATR (Average True Range) measures how much a stock moves per day on average.
# It is the foundation of professional risk management.
#
# Plain-English meaning:
#   • ATR = $3 means the stock typically moves $3 up or down per day
#   • As a % of price: a $100 stock with ATR=$3 has ATR% = 3%
#
# We use ATR% to:
#   1. Score volatility (high ATR% = harder to manage risk = lower score)
#   2. Set stop-loss distance (stop = entry - 1.5 × ATR)
#   3. Set take-profit target (target = entry + 2.5 × ATR)

ATR_STOP_LOSS_MULTIPLIER:   float = float(os.getenv("ATR_STOP_MULTIPLIER", "1.5"))
ATR_TAKE_PROFIT_MULTIPLIER: float = float(os.getenv("ATR_TP_MULTIPLIER", "2.5"))

# ATR% scoring thresholds:
ATR_PCT_IDEAL: float = 0.015  # 1.5% or less = very manageable = score 100
ATR_PCT_HIGH:  float = 0.030  # 3% = score starts dropping
ATR_PCT_MAX:   float = 0.060  # 6% = score hits 0 (hard filter triggers at 8%)

# ─────────────────────────────────────────────────────────────────────────────
# STOCHASTIC SETTINGS
# ─────────────────────────────────────────────────────────────────────────────
# The Stochastic oscillator measures WHERE the current price sits within its
# recent high-low range. Like RSI, it runs 0–100.
#
# Plain-English meaning:
#   • Stochastic near 0   = price is near recent lows (possibly oversold)
#   • Stochastic near 100 = price is near recent highs (possibly overbought)
#   • Best entry zone: 30–70 (not extreme, room to move)

STOCH_OVERBOUGHT: float = 80.0   # Above this = penalty
STOCH_OVERSOLD:   float = 20.0   # Below this = small penalty (buying dip)
STOCH_EXTREME_OB: float = 85.0   # Above this = large penalty

# ─────────────────────────────────────────────────────────────────────────────
# BOLLINGER BANDS SETTINGS
# ─────────────────────────────────────────────────────────────────────────────
# Bollinger Bands are three lines drawn around a stock's price:
#   • Upper band = "expensive" relative to recent history
#   • Middle band = average (typically 20-day moving average)
#   • Lower band = "cheap" relative to recent history
#
# We use BB position (0.0 to 1.0) to score entry timing:
#   • 0.0 = price at lower band (potential support, good entry in uptrend)
#   • 0.5 = price at middle band (ideal — riding the trend, not chasing)
#   • 1.0 = price at upper band (extended — dangerous to buy here)
#   • > 1.0 = price above upper band (extreme — strong penalty)

BB_IDEAL_LOW:     float = 0.30   # Ideal zone starts here (lower half)
BB_IDEAL_HIGH:    float = 0.50   # Ideal zone ends here (middle)
BB_EXTENDED_LOW:  float = 0.70   # Above here = "extended", score drops
BB_EXTENDED_HIGH: float = 1.00   # At/above here = score = 0

# ─────────────────────────────────────────────────────────────────────────────
# MOVING AVERAGE PERIODS
# ─────────────────────────────────────────────────────────────────────────────
# Moving averages smooth out short-term price noise to show the underlying trend.
# SMA (Simple) and EMA (Exponential) both do this — EMA reacts faster to
# recent price changes.

SMA_PERIOD: int = 20   # 20-day Simple Moving Average
EMA_PERIOD: int = 20   # 20-day Exponential Moving Average

# ─────────────────────────────────────────────────────────────────────────────
# RISK MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────
# Professional risk management says: never risk more than 1–2% of your total
# account on a single trade. If you have $10,000, you risk at most $100–$200
# per trade. This keeps any single loss from being devastating.

RISK_PER_TRADE_USD:  float = float(os.getenv("RISK_PER_TRADE_USD", "500.0"))
ACCOUNT_SIZE_USD:    float = float(os.getenv("ACCOUNT_SIZE_USD", "50000.0"))

# Maximum risk as a % of account (used for the risk note — not enforced in code)
MAX_RISK_PCT: float = 0.02  # 2% of account

# ─────────────────────────────────────────────────────────────────────────────
# CONFIDENCE SCORE WEIGHTS
# ─────────────────────────────────────────────────────────────────────────────
# The confidence score (0–100) measures how much you can trust the recommendation.
# It combines:
#   1. How complete the indicator data is (did all 10 indicators return values?)
#   2. How much the factor scores agree with each other
#   3. How strong the trend is (ADX)

CONFIDENCE_WEIGHTS: dict = {
    "data_completeness": 0.40,  # 40% weight on having complete data
    "signal_agreement":  0.35,  # 35% weight on factor score consensus
    "adx_strength":      0.25,  # 25% weight on trend strength
}

# ─────────────────────────────────────────────────────────────────────────────
# PENALTY POINT VALUES
# ─────────────────────────────────────────────────────────────────────────────
# These accumulate during scoring. The sum is then multiplied by
# FACTOR_WEIGHTS["risk_penalty"] (0.05) to produce the final deduction.
# Maximum deduction = 5 points from the total score (100 × 0.05 = 5).

PENALTY_POINTS: dict = {
    "rsi_overbought":          25,   # RSI > RSI_OVERBOUGHT (75)
    "rsi_oversold":            20,   # RSI < RSI_OVERSOLD (30) — catching a falling knife
    "stoch_extreme_overbought": 20,  # Stoch K > 85
    "macd_bearish_in_uptrend": 15,   # MACD histogram negative while price > SMA
    "mtf_macd_conflict":       15,   # Daily and 4h MACD histograms disagree in sign
    "missing_rsi":             10,   # No RSI data returned from API
    "missing_macd":            10,   # No MACD data returned from API
    "ma_recently_crossed":     10,   # Price between SMA and EMA (trend uncertain)
    "missing_adx":              5,   # No ADX data returned from API
}

# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO MONITORING
# ─────────────────────────────────────────────────────────────────────────────
# Settings that control how existing holdings are evaluated.
# These are separate from the entry-scoring settings above.

# Path to the JSON file that stores portfolio holdings
PORTFOLIO_FILE: str = os.getenv("PORTFOLIO_FILE", "portfolio.json")

# ── Trailing Stop ──────────────────────────────────────────────────────────
# Trailing stops ratchet up with the stock price, locking in gains.
# A trailing stop of 8% means: if the stock falls 8% from its highest
# price since you bought it, exit the position.
#
# Example: You bought AAPL at $170. It rose to $195 (the "peak").
# Trailing stop = $195 × (1 - 0.08) = $179.40
# If AAPL falls to $179.40, you sell — even though it's still above your cost.
# This protects the profit you've already made.

TRAILING_STOP_DEFAULT_PCT: float = float(os.getenv("TRAILING_STOP_PCT", "0.08"))  # 8%

# ── Hard Stop for Holdings (without explicit stop set) ─────────────────────
# For holdings where the user hasn't specified a stop-loss, we compute one
# using ATR. For existing positions we use a wider multiplier (2.0×) than
# for new entries (1.5×) to avoid being stopped out on normal volatility.

HOLD_STOP_ATR_MULTIPLIER: float = 2.0

# ── Hold Quality Score Weights ─────────────────────────────────────────────
# How much each factor contributes to the "is this worth holding?" score.
# Different from the opportunity entry weights — holding needs different emphasis.

HOLD_QUALITY_WEIGHTS: dict = {
    "trend_integrity":    0.30,  # Is the uptrend still intact?
    "momentum_health":    0.25,  # Is momentum still positive (not deteriorating)?
    "volume_support":     0.20,  # Is volume still confirming the move?
    "technical_position": 0.15,  # Is price in a healthy position (not damaged/extended)?
    "multi_timeframe":    0.10,  # Do daily and 4h still agree?
}

# ── Hold Recommendation Thresholds ────────────────────────────────────────
# These map sell_risk_score + hold_quality_score to a recommendation label.

SELL_RISK_THRESHOLDS: dict = {
    "sell":          70,   # Sell risk >= 70 → Sell
    "trim":          50,   # Sell risk >= 50 → Trim
    "watch_closely": 30,   # Sell risk >= 30 → Watch Closely
    # Below 30 + Hold Quality >= 50 → Hold
}

# ── Sell Risk Penalty Points ───────────────────────────────────────────────
# These accumulate to produce the Sell Risk Score (0-100).
# Higher total = more reason to consider exiting.

SELL_RISK_PENALTIES: dict = {
    "hard_stop_breach":        40,  # Price below hard stop → urgent
    "trailing_stop_breach":    35,  # Price below trailing stop → locked-in gain at risk
    "below_sma20":             15,  # Price below 20-day MA → trend broken
    "rsi_deteriorating":       15,  # RSI below 45 → momentum fading
    "macd_histogram_negative": 20,  # MACD histogram negative → selling pressure
    "adx_weakening":           10,  # ADX below 20 → trend strength fading
    "stoch_overbought_rollover": 15, # Stochastic overbought AND rolling over
    "stoch_overbought_only":    8,  # Stochastic overbought (no rollover)
    "high_volatility":         10,  # ATR% > 3% → risk expanding
    "high_drawdown":           10,  # Drawdown from peak > 10%
    "extreme_drawdown":        20,  # Drawdown from peak > 20% (replaces high_drawdown)
    "concentration_risk":      15,  # Position > 15% of portfolio
    "mtf_early_warning":       10,  # 4h bearish while daily still bullish
}

# ── Drawdown Thresholds ────────────────────────────────────────────────────
# How far a stock can fall from its peak before it's a concern.
DRAWDOWN_ALARM_PCT:    float = 0.10  # 10% drawdown from peak = warning
DRAWDOWN_CRITICAL_PCT: float = 0.20  # 20% drawdown from peak = strong sell signal

# ── Concentration Risk ─────────────────────────────────────────────────────
# If a single position exceeds this % of total portfolio value, it's considered
# oversized and triggers a "Trim" recommendation.
MAX_POSITION_PCT: float = float(os.getenv("MAX_POSITION_PCT", "0.15"))  # 15%

# ── RSI Deterioration Threshold (for holders) ─────────────────────────────
# For new entries, ideal RSI is 40-65. For existing holders, we want RSI
# to stay above 45 — falling below this suggests momentum is turning against us.
RSI_DETERIORATION_THRESHOLD: float = 45.0

# ── ADX Decline Threshold (for holders) ────────────────────────────────────
# A trend we're riding should stay above ADX 20. Below this, the trend
# is losing structure and the position becomes riskier to hold.
ADX_DECLINE_THRESHOLD: float = 20.0

# ── Profit Taking ──────────────────────────────────────────────────────────
# Triggers a "Take Profit" recommendation when the position shows substantial
# gains AND signs of momentum stalling.
PROFIT_TAKE_MIN_GAIN_PCT: float = 0.20  # At least 20% gain to consider taking profit

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
