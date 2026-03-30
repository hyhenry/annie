"""
main.py — Command-line entry point for the Annie stock recommendation engine.

USAGE EXAMPLES:
    # Run with built-in sample data (no API key needed):
    python main.py --mock

    # Run with sample data and verbose output:
    python main.py --mock --verbose

    # Analyse specific tickers using live TAAPI data:
    python main.py --tickers AAPL,MSFT,NVDA --verbose

    # Scan every US stock on TAAPI (mock — instant; live — ~90 min):
    python main.py --all --mock --only-buy --top 10

    # Load tickers from a file and save output to JSON:
    python main.py --tickers-file tickers.json --output results.json

    # Only show Buy recommendations, top 5:
    python main.py --mock --no-avoid --top 5

    # Only show stocks scoring above 70:
    python main.py --mock --min-score 70

    # Show this help message:
    python main.py --help

ENVIRONMENT:
    Copy .env.example to .env and fill in your TAAPI_SECRET before using
    live data. Without a key, use --mock to test the engine.
"""

import argparse
import json
import logging
import os
import sys

# Load .env file BEFORE importing config, so env vars are available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed — rely on actual environment variables

import config
from engine import run_engine, load_tickers, reports_to_json
from taapi_client import fetch_all_symbols


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING SETUP
# ─────────────────────────────────────────────────────────────────────────────

def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the application."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format=config.LOG_FORMAT,
        stream=sys.stderr,  # logs to stderr so stdout remains clean JSON
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI ARGUMENT PARSER
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    """Define all CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="annie",
        description=(
            "Annie — Stock Recommendation Engine\n"
            "Analyses technical indicators to score and rank stocks for swing trading.\n\n"
            "This tool is for educational and decision-support purposes only.\n"
            "It is NOT financial advice."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python main.py --mock --verbose          # run with sample data, verbose output
  python main.py --tickers AAPL,MSFT       # live data for two stocks
  python main.py --mock --no-avoid --top 3 # top 3 non-Avoid stocks
  python main.py --mock --output out.json  # save results to file
  python main.py --all --mock --only-buy   # scan all ~467 symbols (mock)

disclaimer:
  This engine is a decision-support tool. All outputs are based on technical
  indicators only and do not constitute financial advice. Past indicator
  patterns do not guarantee future results. Always do your own research
  and consult a qualified financial advisor before investing.
        """,
    )

    # ── Input ──────────────────────────────────────────────────────────────
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--tickers", "-t",
        type=str,
        metavar="AAPL,MSFT,...",
        help="Comma-separated list of stock symbols to analyse (e.g. AAPL,MSFT,NVDA). "
             "Overrides --tickers-file.",
    )
    input_group.add_argument(
        "--tickers-file", "-f",
        type=str,
        default="tickers.json",
        metavar="PATH",
        help="Path to a JSON file containing a list of ticker symbols. "
             "Default: tickers.json",
    )
    input_group.add_argument(
        "--all",
        action="store_true",
        default=False,
        dest="all_symbols",
        help="Scan every US stock available on TAAPI (~467 symbols). "
             "Fetches the list from GET /exchange-symbols, then scores each one. "
             "Warning: on the free tier this takes ~90 min due to rate limiting. "
             "Use --top and --only-buy to filter results. "
             "In mock mode the bundled all_symbols.json list is used instantly.",
    )

    # ── Mode ───────────────────────────────────────────────────────────────
    parser.add_argument(
        "--mock", "-m",
        action="store_true",
        default=False,
        help="Run with built-in sample data — no API key or internet required. "
             "Useful for testing and exploring the output format.",
    )
    parser.add_argument(
        "--bulk",
        action="store_true",
        default=False,
        help="Use the TAAPI Pro bulk endpoint (faster, requires Pro subscription). "
             "Without this flag, the free-tier individual endpoints are used.",
    )

    # ── Timeframes ─────────────────────────────────────────────────────────
    parser.add_argument(
        "--primary-interval",
        type=str,
        default=None,
        metavar="1d",
        help="Primary analysis timeframe (default: 1d). "
             "TAAPI codes: 1m 5m 15m 30m 1h 2h 4h 12h 1d 1w",
    )
    parser.add_argument(
        "--secondary-interval",
        type=str,
        default=None,
        metavar="4h",
        help="Secondary (confirmation) timeframe (default: 4h).",
    )

    # ── Output ─────────────────────────────────────────────────────────────
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        metavar="PATH",
        help="Write JSON output to this file path in addition to stdout. "
             "Example: --output results.json",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        default=True,
        help="Pretty-print JSON output with indentation (default: True).",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        default=False,
        help="Output compact (minified) JSON instead of pretty-printed.",
    )

    # ── Filters ────────────────────────────────────────────────────────────
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        metavar="SCORE",
        help="Only include stocks with a final score >= this value. "
             "Example: --min-score 60 shows only Watch and Buy.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        metavar="N",
        help="Only output the top N stocks by score. Example: --top 5",
    )
    parser.add_argument(
        "--no-avoid",
        action="store_true",
        default=False,
        help="Exclude Avoid recommendations from output.",
    )
    parser.add_argument(
        "--only-buy",
        action="store_true",
        default=False,
        help="Only show Buy recommendations.",
    )

    # ── Verbosity ──────────────────────────────────────────────────────────
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Print per-ticker progress and summary to stderr during analysis.",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        metavar="LEVEL",
        help="Log level (default: INFO). Use DEBUG for maximum detail.",
    )

    return parser


# ─────────────────────────────────────────────────────────────────────────────
# INPUT VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

def validate_environment(mock_mode: bool) -> bool:
    """
    Check that required configuration is present before running.

    Returns True if all checks pass, False if there is a blocking issue.
    Prints helpful error messages to stderr.
    """
    if not mock_mode and not config.TAAPI_SECRET:
        print(
            "\n[ERROR] TAAPI_SECRET is not set.\n"
            "\nTo fix this:\n"
            "  1. Copy .env.example to .env\n"
            "  2. Add your TAAPI API key to the TAAPI_SECRET= line\n"
            "  3. Get a free key at https://taapi.io\n"
            "\nAlternatively, run with --mock to use built-in sample data:\n"
            "  python main.py --mock --verbose\n",
            file=sys.stderr,
        )
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# CONSOLE SUMMARY PRINTER
# ─────────────────────────────────────────────────────────────────────────────

def print_summary(reports: list) -> None:
    """
    Print a human-readable rankings table to stderr.
    (The structured JSON still goes to stdout.)
    """
    if not reports:
        print("\n[No results to display]\n", file=sys.stderr)
        return

    print("\n" + "─" * 68, file=sys.stderr)
    print(f"  {'Rank':<5}{'Ticker':<8}{'Score':<8}{'Rec':<8}{'Confidence':<12}", file=sys.stderr)
    print("─" * 68, file=sys.stderr)

    icons = {"Buy": "★ BUY  ", "Watch": "◎ WATCH", "Avoid": "✗ AVOID"}

    for i, r in enumerate(reports, 1):
        icon = icons.get(r.recommendation, "?")
        print(
            f"  {i:<5}{r.ticker:<8}{r.score:<8.1f}{icon:<8}  {r.confidence:.0f}%",
            file=sys.stderr,
        )
        if r.trade_plan:
            tp = r.trade_plan
            print(
                f"       Entry: ${tp['entry']:.2f}  |  "
                f"Stop: ${tp['stop_loss']:.2f}  |  "
                f"Target: ${tp['take_profit']:.2f}  |  "
                f"Shares: {tp['position_size_shares']}  |  "
                f"R/R: {tp['reward_risk_ratio']:.1f}:1",
                file=sys.stderr,
            )

    print("─" * 68, file=sys.stderr)
    buy_count   = sum(1 for r in reports if r.recommendation == "Buy")
    watch_count = sum(1 for r in reports if r.recommendation == "Watch")
    avoid_count = sum(1 for r in reports if r.recommendation == "Avoid")
    print(
        f"  Summary: {buy_count} Buy | {watch_count} Watch | {avoid_count} Avoid",
        file=sys.stderr,
    )
    print("─" * 68 + "\n", file=sys.stderr)
    print(
        "  DISCLAIMER: This is a decision-support tool, not financial advice.\n"
        "  Always do your own research before making any investment decisions.\n",
        file=sys.stderr,
    )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    """
    Entry point.

    Returns:
        0 on success, non-zero on failure (for use in shell scripts).
    """
    parser = build_parser()
    args   = parser.parse_args()

    # ── Configure logging ──────────────────────────────────────────────────
    log_level = args.log_level or os.getenv("LOG_LEVEL", "INFO")
    if args.verbose and log_level == "INFO":
        log_level = "INFO"  # verbose already at INFO level
    setup_logging(log_level)

    # ── Apply CLI overrides to config ──────────────────────────────────────
    # These override what's in .env / config.py for this run only.
    if args.mock:
        config.MOCK_MODE = True
    if args.bulk:
        config.USE_BULK_API = True

    # ── Validate environment ───────────────────────────────────────────────
    if not validate_environment(config.MOCK_MODE):
        return 1

    # ── Load tickers ───────────────────────────────────────────────────────
    if args.all_symbols:
        tickers = fetch_all_symbols()
        print(
            f"[INFO] Loaded {len(tickers)} symbols from TAAPI exchange-symbols endpoint.",
            file=sys.stderr,
        )
        if not config.MOCK_MODE:
            est_minutes = round(len(tickers) * config.TAAPI_RATE_LIMIT_DELAY * 10 / 60)
            print(
                f"[INFO] Estimated scan time on free tier: ~{est_minutes} minutes "
                f"({len(tickers)} tickers × ~10 API calls each at "
                f"{config.TAAPI_RATE_LIMIT_DELAY}s/call).\n"
                f"       Use --top N or --only-buy to stop early.",
                file=sys.stderr,
            )
    elif args.tickers:
        # Tickers provided directly on the command line
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        # Load from JSON file
        tickers = load_tickers(args.tickers_file)

    if not tickers:
        print(
            f"[ERROR] No tickers to analyse. "
            f"Check --tickers or --tickers-file ({args.tickers_file}).",
            file=sys.stderr,
        )
        return 1

    # ── Run engine ─────────────────────────────────────────────────────────
    reports = run_engine(
        tickers=tickers,
        primary_interval=args.primary_interval,
        secondary_interval=args.secondary_interval,
        output_file=args.output,   # engine also writes file if requested
        verbose=args.verbose,
    )

    # ── Apply output filters ───────────────────────────────────────────────
    if args.only_buy:
        reports = [r for r in reports if r.recommendation == "Buy"]
    elif args.no_avoid:
        reports = [r for r in reports if r.recommendation != "Avoid"]

    if args.min_score > 0:
        reports = [r for r in reports if r.score >= args.min_score]

    if args.top:
        reports = reports[: args.top]

    # ── Print summary table to stderr ──────────────────────────────────────
    if args.verbose:
        print_summary(reports)

    # ── Emit JSON to stdout ────────────────────────────────────────────────
    output_data = reports_to_json(reports)
    indent = None if args.compact else 2
    json_str = json.dumps(output_data, indent=indent, default=str)
    print(json_str)

    return 0


if __name__ == "__main__":
    sys.exit(main())
