# Annie — Stock Intelligence Engine

A multifactor swing-trade scoring and portfolio monitoring engine built on [TAAPI.io](https://taapi.io) technical indicators. Runs fully offline with built-in mock data — no API key required to get started.

## Features

- **Scanner** — Score any US stock 0-100 using 7 weighted technical factors (trend, momentum, volume, entry timing, volatility, multi-timeframe, risk penalty). Outputs Buy / Watch / Avoid with a full trade plan (entry, stop-loss, take-profit, position size).
- **Portfolio Monitor** — Track existing holdings with a Hold Quality Score and Sell Risk Score. Generates Hold / Watch Closely / Trim / Sell / Take Profit recommendations with trailing stop management.
- **Streamlit UI** — Four-tab web app: Portfolio dashboard, Scanner, Manage Holdings, Settings.
- **CLI** — JSON output to stdout; pipe into files or downstream tools.
- **Mock mode** — Five pre-built scenarios (strong buy → auto-avoid) so the full engine works without any API key.

## Quickstart

```bash
# 1. Install dependencies (Python 3.9+)
pip install -r requirements.txt

# 2. Run the scanner in mock mode (no API key needed)
python main.py --mock --verbose

# 3. Launch the Streamlit app in mock mode
MOCK_MODE=true streamlit run app.py
```

## Setup with a Real API Key

```bash
cp .env.example .env
# Edit .env and set TAAPI_SECRET=your_key_here
streamlit run app.py
```

Sign up for a free TAAPI key at [taapi.io](https://taapi.io) — no credit card required.

## Project Structure

```
annie/
├── config.py             # All weights, thresholds, and settings (single source of truth)
├── taapi_client.py       # TAAPI HTTP client with rate limiting, retry, and mock data
├── indicators.py         # Parse raw API response → IndicatorData; normalize each to 0-100
├── scoring.py            # Weighted factor scores → Opportunity Score + recommendation
├── risk.py               # ATR-based stop-loss, take-profit, and position sizing
├── engine.py             # Orchestrate full scanner pipeline; rank and sort results
├── main.py               # CLI entry point (argparse, JSON output)
│
├── portfolio.py          # Holding / Portfolio dataclasses; JSON persistence
├── hold_scoring.py       # Hold Quality Score + Sell Risk Score models
├── portfolio_monitor.py  # Orchestrate portfolio analysis; HoldingReport output
│
├── app.py                # Streamlit web frontend (4 tabs)
├── portfolio.json        # Your portfolio (edited via the Manage tab or directly)
├── tickers.json          # Default ticker list for the scanner
│
├── tests/
│   └── test_scoring.py   # 83 unit tests
└── .env.example          # Environment variable template
```

## Scoring Model

### Opportunity Score (Scanner)

| Factor | Weight | Signal Used |
|---|---|---|
| Trend | 25% | Price vs SMA/EMA, ADX strength |
| Momentum | 20% | MACD histogram, RSI (ideal zone: 40-65) |
| Volume | 15% | OBV proxy + MA/MACD alignment |
| Entry Timing | 15% | Bollinger Band position, Stochastic %K |
| Volatility | 10% | ATR% inversely scaled (lower = better) |
| Multi-Timeframe | 10% | Daily vs 4h RSI/MACD/MA agreement |
| Risk Penalty | 5% | Overbought, conflicting signals, missing data |

**Recommendation bands:** Buy ≥ 80 · Watch ≥ 60 · Avoid < 60

**Hard filters (auto-Avoid):** price < $5 · avg volume < 500K · ATR% > 8%

### Hold Quality Score (Portfolio Monitor)

Weighted across trend health, momentum quality, multi-timeframe confirmation, and volatility — tuned to favour continuation over breakout.

### Sell Risk Score (Portfolio Monitor)

Additive penalty model. Key triggers: trailing stop breach, hard stop breach, RSI deterioration, drawdown alarm, concentration risk, MTF divergence, profit target reached.

**Recommendation thresholds:** Sell ≥ 70 · Trim ≥ 50 · Watch Closely ≥ 30 · Hold < 30

## CLI Reference

```bash
# Scan specific tickers
python main.py --mock --tickers AAPL,MSFT,NVDA

# Filter output
python main.py --mock --only-buy --top 5

# Save results
python main.py --mock --output results.json

# All options
python main.py --help
```

## Running Tests

```bash
pytest tests/ -v
pytest tests/ --cov=. --cov-report=term-missing
```

## Configuration

All tunable parameters live in `config.py`:

- Scoring weights and recommendation thresholds
- Risk management defaults (ATR multipliers, position sizing)
- Hard filter thresholds
- Portfolio monitoring thresholds (trailing stop %, drawdown alarms, concentration limit)

Override any value at runtime via environment variables (see `.env.example`).

## Disclaimer

Annie is a personal research and learning tool. Nothing it outputs is financial advice. Always do your own research before making any investment decision.
