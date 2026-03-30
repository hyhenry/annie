"""
app.py — Streamlit frontend for the Annie stock intelligence engine.

HOW TO RUN:
    streamlit run app.py

    # With mock data (no API key needed):
    MOCK_MODE=true streamlit run app.py

TABS:
    📊 Portfolio   — Monitor existing holdings (Hold / Trim / Sell signals)
    🔍 Scanner     — Scan new tickers for buy opportunities
    ✏️  Portfolio   — Add, edit, or remove holdings
    ⚙️  Settings   — View current configuration

This file is the UI layer only — all business logic lives in the other modules.
"""

import json
import os
from datetime import date, datetime
from typing import List, Optional

import pandas as pd
import streamlit as st

# ─── Load env before config ───────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import config
from portfolio import Holding, Portfolio, load_portfolio, save_portfolio
from portfolio_monitor import HoldingReport, monitor_portfolio, reports_to_json as holding_reports_to_json
from engine import run_engine, load_tickers, reports_to_json as scan_reports_to_json
from taapi_client import fetch_all_symbols

# ─────────────────────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Annie — Stock Intelligence",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# THEME CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

REC_COLORS = {
    "Sell":          ("#FF4B4B", "white"),
    "Trim":          ("#FF8C00", "white"),
    "Watch Closely": ("#DAA520", "white"),
    "Hold":          ("#1E8E3E", "white"),
    "Take Profit":   ("#1A73E8", "white"),
    "Buy":           ("#1E8E3E", "white"),
    "Watch":         ("#DAA520", "white"),
    "Avoid":         ("#5F6368", "white"),
}

REC_EMOJI = {
    "Sell":          "🔴",
    "Trim":          "🟠",
    "Watch Closely": "🟡",
    "Hold":          "🟢",
    "Take Profit":   "🔵",
    "Buy":           "🟢",
    "Watch":         "🟡",
    "Avoid":         "⚫",
}

# Urgency sort order for portfolio recommendations
_URGENCY = {"Sell": 0, "Trim": 1, "Watch Closely": 2, "Take Profit": 3, "Hold": 4}


def rec_badge(rec: str) -> str:
    """Render a coloured recommendation badge using inline HTML."""
    bg, fg = REC_COLORS.get(rec, ("#5F6368", "white"))
    return (
        f'<span style="background:{bg};color:{fg};padding:3px 12px;'
        f'border-radius:14px;font-size:0.82em;font-weight:700;'
        f'letter-spacing:0.03em;">{rec.upper()}</span>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────────────────────────────────────

def _init_state():
    defaults = {
        "portfolio_reports":  None,   # List[HoldingReport] from last portfolio analysis
        "scan_results":       None,   # List[StockReport] from last opportunity scan
        "portfolio_path":     config.PORTFOLIO_FILE,
        "last_portfolio_run": None,
        "last_scan_run":      None,
        "error":              None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_state()


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _check_plan_restriction(results) -> None:
    """
    Show a clear error banner if any result indicates a TAAPI plan restriction.
    The free tier only covers crypto — US stocks require a paid plan.
    """
    if not results:
        return
    hit = any(
        r.error == "plan_restriction" or "plan_restriction" in (r.filter_failures or [])
        for r in results
    )
    if hit:
        st.error(
            "**TAAPI plan does not support US stocks.**\n\n"
            "The free tier is limited to crypto (BTC/USDT, ETH/USDT, etc.) on Binance. "
            "To scan US equities you need a paid plan.\n\n"
            "**Options:**\n"
            "- Enable **Mock Mode** (toggle in sidebar) to explore with built-in sample data — no API key needed.\n"
            "- Upgrade your TAAPI plan at [taapi.io/pricing](https://taapi.io/pricing/).",
            icon="🔒",
        )


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────

def render_sidebar():
    with st.sidebar:
        st.markdown("## 📈 Annie")
        st.caption("Stock Intelligence Engine")
        st.divider()

        # Mode toggle
        mock = st.toggle(
            "🎭 Mock Mode",
            value=config.MOCK_MODE,
            help="Run with built-in sample data — no API key required. "
                 "Great for exploring the engine without a TAAPI subscription.",
        )
        config.MOCK_MODE = mock

        if not mock:
            key = st.text_input(
                "TAAPI Secret Key",
                type="password",
                value=config.TAAPI_SECRET or "",
                placeholder="Paste your key from taapi.io …",
            )
            if key:
                config.TAAPI_SECRET = key

        st.divider()

        # Portfolio file path
        path = st.text_input(
            "Portfolio file",
            value=st.session_state.portfolio_path,
            help="Path to the JSON file that stores your holdings.",
        )
        st.session_state.portfolio_path = path

        if st.button("🔄 Refresh Portfolio", use_container_width=True, type="primary"):
            _run_portfolio_analysis()
            st.rerun()

        st.divider()
        st.caption(
            "⚠️ **Disclaimer:** This tool is for educational and "
            "decision-support purposes only. It is not financial advice. "
            "Always do your own research."
        )


def _run_portfolio_analysis():
    """Fetch live/mock data for all holdings and store results in session state."""
    path      = st.session_state.portfolio_path
    portfolio = load_portfolio(path)

    if not portfolio.holdings:
        st.session_state.error = (
            f"No holdings found in **{path}**. "
            "Go to the ✏️ Manage Portfolio tab to add your first position."
        )
        return

    with st.spinner(f"Analysing {len(portfolio.holdings)} holding(s) …"):
        try:
            reports = monitor_portfolio(portfolio, update_peaks=True)
            save_portfolio(portfolio, path)        # persist updated peak prices
            st.session_state.portfolio_reports  = reports
            st.session_state.last_portfolio_run = datetime.now().strftime("%H:%M:%S")
            st.session_state.error              = None
        except Exception as exc:
            err = str(exc)
            if "plan" in err.lower() or "free tier" in err.lower() or "taapi" in err.lower():
                err = (
                    "**TAAPI plan does not support US stocks.** "
                    "Enable **Mock Mode** in the sidebar, or upgrade your plan at "
                    "[taapi.io/pricing](https://taapi.io/pricing/)."
                )
            st.session_state.error = err


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1: PORTFOLIO MONITOR
# ─────────────────────────────────────────────────────────────────────────────

def render_portfolio_tab():
    st.header("📊 Portfolio Monitor")

    if st.session_state.error:
        st.error(st.session_state.error)

    reports: Optional[List[HoldingReport]] = st.session_state.portfolio_reports

    if reports is None:
        st.info(
            "**No analysis yet.** Click **🔄 Refresh Portfolio** in the sidebar "
            "to fetch live data and evaluate your holdings."
        )
        if config.MOCK_MODE:
            st.markdown(
                "> 🎭 **Mock mode is ON** — the sample portfolio in `portfolio.json` "
                "will be used. Hit Refresh to see the results."
            )
        with st.expander("📋 Sample portfolio loaded — expand to preview"):
            try:
                p = load_portfolio(st.session_state.portfolio_path)
                if p.holdings:
                    rows = [
                        {"Ticker": h.ticker, "Shares": h.shares, "Avg Cost": f"${h.avg_cost:.2f}",
                         "Entry Date": h.entry_date}
                        for h in p.holdings
                    ]
                    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                else:
                    st.caption("Portfolio file is empty or not found.")
            except Exception:
                st.caption("Could not load portfolio preview.")
        return

    # ── Summary metrics ──────────────────────────────────────────────────────
    _render_portfolio_summary(reports)

    st.divider()

    # ── Holdings overview table ───────────────────────────────────────────────
    st.subheader("Holdings Overview")
    _render_holdings_table(reports)

    st.divider()

    # ── Individual holding cards ──────────────────────────────────────────────
    st.subheader("Holding Details")
    for report in reports:
        _render_holding_card(report)

    if st.session_state.last_portfolio_run:
        st.caption(f"Last refreshed at {st.session_state.last_portfolio_run}")


def _render_portfolio_summary(reports: List[HoldingReport]):
    valid = [r for r in reports if r.error is None]

    total_value   = sum(r.position_value_usd for r in valid)
    total_cost    = sum(r.holding.shares * r.holding.avg_cost for r in valid)
    total_pnl     = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0

    n_sell  = sum(1 for r in valid if r.recommendation == "Sell")
    n_trim  = sum(1 for r in valid if r.recommendation == "Trim")
    n_watch = sum(1 for r in valid if r.recommendation == "Watch Closely")
    n_tp    = sum(1 for r in valid if r.recommendation == "Take Profit")
    n_hold  = sum(1 for r in valid if r.recommendation == "Hold")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(
        "Portfolio Value",
        f"${total_value:,.0f}",
        f"{total_pnl_pct:+.1f}%",
    )
    c2.metric(
        "Unrealised P&L",
        f"${total_pnl:+,.0f}",
        delta_color="normal" if total_pnl >= 0 else "inverse",
    )
    c3.metric("Holdings", str(len(valid)))
    c4.metric(
        "🟢 Hold / 🔵 Take Profit",
        f"{n_hold}  /  {n_tp}",
    )
    c5.metric(
        "⚠️ Alerts",
        f"🔴 {n_sell}  🟠 {n_trim}  🟡 {n_watch}",
    )

    # Alert banner for urgent actions
    urgent = [r for r in valid if r.recommendation in ("Sell", "Trim")]
    if urgent:
        tickers = ", ".join(r.holding.ticker for r in urgent)
        st.warning(
            f"**Action required:** {tickers} — "
            + ("Review and consider exiting or reducing these positions." if len(urgent) > 1
               else f"Review the {urgent[0].recommendation.lower()} signal below.")
        )


def _render_holdings_table(reports: List[HoldingReport]):
    rows = []
    for r in reports:
        emoji = REC_EMOJI.get(r.recommendation, "❓")
        pnl_str = f"{r.unrealized_pnl_pct * 100:+.1f}%"
        rows.append({
            "Ticker":       r.holding.ticker,
            "Shares":       int(r.holding.shares),
            "Avg Cost":     r.holding.avg_cost,
            "Current":      r.current_price,
            "P&L %":        r.unrealized_pnl_pct * 100,
            "Value ($)":    r.position_value_usd,
            "Hold Quality": r.hold_quality_score,
            "Sell Risk":    r.sell_risk_score,
            "Recommendation": f"{emoji}  {r.recommendation}",
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        column_config={
            "Avg Cost":     st.column_config.NumberColumn("Avg Cost",    format="$%.2f"),
            "Current":      st.column_config.NumberColumn("Price",       format="$%.2f"),
            "P&L %":        st.column_config.NumberColumn("P&L %",       format="%.1f%%"),
            "Value ($)":    st.column_config.NumberColumn("Value",       format="$%.0f"),
            "Hold Quality": st.column_config.ProgressColumn(
                "Hold Quality", min_value=0, max_value=100, format="%.0f"
            ),
            "Sell Risk":    st.column_config.ProgressColumn(
                "Sell Risk",    min_value=0, max_value=100, format="%.0f"
            ),
            "Recommendation": st.column_config.TextColumn("Signal", width="medium"),
        },
        hide_index=True,
        use_container_width=True,
    )


def _render_holding_card(report: HoldingReport):
    emoji = REC_EMOJI.get(report.recommendation, "❓")
    pnl_str = f"{report.unrealized_pnl_pct * 100:+.1f}%"
    label = (
        f"{emoji} **{report.holding.ticker}** — "
        f"{report.recommendation}  ·  P&L: {pnl_str}"
        f"  ·  Hold Quality: {report.hold_quality_score:.0f}  ·  "
        f"Sell Risk: {report.sell_risk_score:.0f}"
    )

    with st.expander(label, expanded=(report.recommendation in ("Sell", "Trim"))):
        if report.error:
            st.error(f"Analysis error: {report.error}")
            return

        # Badge + key numbers row
        st.markdown(rec_badge(report.recommendation), unsafe_allow_html=True)
        st.markdown("")

        left, right = st.columns([3, 2])

        with left:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Price",        f"${report.current_price:.2f}")
            m2.metric("P&L",          f"${report.unrealized_pnl_usd:+,.0f}", pnl_str)
            m3.metric("Hard Stop",    f"${report.hard_stop_price:.2f}"    if report.hard_stop_price else "—",
                      delta="⚠️ BREACHED" if report.hard_stop_breach else None,
                      delta_color="inverse" if report.hard_stop_breach else "normal")
            m4.metric("Trailing Stop", f"${report.trailing_stop_price:.2f}",
                      delta="⚠️ BREACHED" if report.trailing_stop_breach else None,
                      delta_color="inverse" if report.trailing_stop_breach else "normal")

            st.markdown("")
            st.markdown(f"**Analysis:** {report.explanation}")

            if report.action_items:
                st.markdown("**Action items:**")
                for item in report.action_items:
                    st.markdown(f"- {item}")

            if report.holding.thesis:
                st.caption(f"📝 Original thesis: *{report.holding.thesis}*")

            if report.drawdown_from_peak_pct is not None and report.drawdown_from_peak_pct > 0.01:
                peak = report.holding.peak_price
                dd_pct = report.drawdown_from_peak_pct * 100
                st.caption(
                    f"📉 Drawdown from peak: {dd_pct:.1f}% "
                    + (f"(peak was ${peak:.2f})" if peak else "")
                )

        with right:
            # Hold Quality factor scores
            st.markdown("**Hold Quality Factors**")
            fs = report.hold_factor_scores
            score_items = [
                ("Trend Integrity",    fs.get("trend_integrity", 0)),
                ("Momentum Health",    fs.get("momentum_health", 0)),
                ("Volume Support",     fs.get("volume_support", 0)),
                ("Technical Position", fs.get("technical_position", 0)),
                ("Multi-Timeframe",    fs.get("multi_timeframe", 0)),
            ]
            for label, score in score_items:
                col_l, col_r = st.columns([2, 1])
                col_l.caption(label)
                col_r.caption(f"{score:.0f}/100")
                st.progress(int(min(100, max(0, score))))

            st.markdown("")

            # Active risk flags
            active_flags = [
                k.replace("_", " ").title()
                for k, v in report.sell_risk_factors.items()
                if isinstance(v, bool) and v
            ]
            if active_flags:
                st.markdown("**⚠️ Active Risk Flags**")
                for flag in active_flags:
                    st.markdown(f"- {flag}")

            # Key indicator snapshot
            st.markdown("")
            st.markdown("**Key Indicators**")
            ind = report.indicators
            if ind:
                rsi_val = ind.get("rsi")
                adx_val = ind.get("adx")
                atr_pct = ind.get("atr_pct")
                macd    = ind.get("macd") or {}
                ind_rows = []
                if rsi_val is not None:
                    ind_rows.append({"Indicator": "RSI", "Value": f"{rsi_val:.1f}"})
                if adx_val is not None:
                    ind_rows.append({"Indicator": "ADX", "Value": f"{adx_val:.1f}"})
                if atr_pct is not None:
                    ind_rows.append({"Indicator": "ATR %", "Value": f"{atr_pct:.1f}%"})
                if macd.get("histogram") is not None:
                    ind_rows.append({"Indicator": "MACD Hist", "Value": f"{macd['histogram']:.3f}"})
                if ind_rows:
                    st.dataframe(
                        pd.DataFrame(ind_rows),
                        hide_index=True,
                        use_container_width=True,
                        height=150,
                    )


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2: OPPORTUNITY SCANNER
# ─────────────────────────────────────────────────────────────────────────────

def render_scanner_tab():
    st.header("🔍 Opportunity Scanner")
    st.caption(
        "Scan a list of stocks for new buy opportunities. "
        "Results are ranked by score — highest first."
    )

    col_input, col_run = st.columns([3, 1])

    with col_input:
        ticker_input = st.text_area(
            "Tickers to scan",
            value=", ".join(load_tickers()),
            height=80,
            help="Comma-separated or one per line. Example: AAPL, MSFT, NVDA",
            placeholder="AAPL, MSFT, NVDA, GOOGL …",
        )

    with col_run:
        st.markdown("<br>", unsafe_allow_html=True)
        run_scan  = st.button("▶  Run Scan",         type="primary",   use_container_width=True)
        scan_all  = st.button("🌐 Scan All Stocks",   type="secondary", use_container_width=True,
                               help="Fetch all ~467 US stocks from TAAPI and score each one. "
                                    "In mock mode this is instant. On the live free tier it "
                                    "takes ~90 minutes due to rate limits.")
        filter_buy_only = st.checkbox("Buy only", value=False)
        min_score = st.slider("Min score", 0, 100, 0, 5)

    # ── Scan a custom list ────────────────────────────────────────────────────
    if run_scan:
        raw_tickers = [t.strip().upper() for t in ticker_input.replace("\n", ",").split(",") if t.strip()]
        if not raw_tickers:
            st.warning("Enter at least one ticker symbol.")
        else:
            with st.spinner(f"Scanning {len(raw_tickers)} ticker(s) …"):
                results = run_engine(raw_tickers, verbose=False)
                st.session_state.scan_results       = results
                st.session_state.last_scan_run      = datetime.now().strftime("%H:%M:%S")
                _check_plan_restriction(results)

    # ── Scan all TAAPI symbols ────────────────────────────────────────────────
    if scan_all:
        all_tickers = fetch_all_symbols()
        if not config.MOCK_MODE:
            est_minutes = round(len(all_tickers) * config.TAAPI_RATE_LIMIT_DELAY * 10 / 60)
            st.info(
                f"**Live mode:** scanning {len(all_tickers)} symbols will take "
                f"~{est_minutes} minutes on the free tier (rate-limited to "
                f"{config.TAAPI_RATE_LIMIT_DELAY}s per call). "
                f"Results will appear below when complete."
            )
        else:
            st.info(
                f"**Mock mode:** scanning {len(all_tickers)} symbols instantly. "
                f"Only AAPL, MSFT, NVDA, TSLA, AMC, and SNDL have rich mock data — "
                f"all other tickers use neutral fallback values and will score ~50 (Watch)."
            )
        with st.spinner(f"Scanning all {len(all_tickers)} TAAPI stocks …"):
            results = run_engine(all_tickers, verbose=False)
            st.session_state.scan_results  = results
            st.session_state.last_scan_run = datetime.now().strftime("%H:%M:%S")
            _check_plan_restriction(results)

    results = st.session_state.scan_results
    if results is None:
        st.info("Enter tickers above and click **▶ Run Scan** to find opportunities.")
        return

    # Apply filters
    filtered = results
    if filter_buy_only:
        filtered = [r for r in filtered if r.recommendation == "Buy"]
    if min_score > 0:
        filtered = [r for r in filtered if r.score >= min_score]

    if not filtered:
        st.info("No results match the current filters.")
        return

    # Summary
    n_buy   = sum(1 for r in filtered if r.recommendation == "Buy")
    n_watch = sum(1 for r in filtered if r.recommendation == "Watch")
    n_avoid = sum(1 for r in filtered if r.recommendation == "Avoid")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Scanned", str(len(results)))
    c2.metric("🟢 Buy",  str(n_buy))
    c3.metric("🟡 Watch", str(n_watch))
    c4.metric("⚫ Avoid", str(n_avoid))

    st.divider()

    # Results table
    st.subheader("Ranked Results")
    rows = []
    for r in filtered:
        emoji = REC_EMOJI.get(r.recommendation, "❓")
        rows.append({
            "Ticker":    r.ticker,
            "Score":     r.score,
            "Signal":    f"{emoji}  {r.recommendation}",
            "Confidence": r.confidence,
            "Trend":     r.factor_scores.get("trend", 0),
            "Momentum":  r.factor_scores.get("momentum", 0),
            "Volume":    r.factor_scores.get("volume", 0),
            "Entry":     r.factor_scores.get("entry_timing", 0),
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        column_config={
            "Score":      st.column_config.ProgressColumn("Score",      min_value=0, max_value=100, format="%.0f"),
            "Confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=100, format="%.0f"),
            "Trend":      st.column_config.ProgressColumn("Trend",      min_value=0, max_value=100, format="%.0f"),
            "Momentum":   st.column_config.ProgressColumn("Momentum",   min_value=0, max_value=100, format="%.0f"),
            "Volume":     st.column_config.ProgressColumn("Volume",     min_value=0, max_value=100, format="%.0f"),
            "Entry":      st.column_config.ProgressColumn("Entry",      min_value=0, max_value=100, format="%.0f"),
            "Signal":     st.column_config.TextColumn("Signal",         width="medium"),
        },
        hide_index=True,
        use_container_width=True,
    )

    # Detail expanders for each result
    st.divider()
    st.subheader("Details")
    for r in filtered:
        emoji = REC_EMOJI.get(r.recommendation, "❓")
        with st.expander(
            f"{emoji} **{r.ticker}** — Score: {r.score:.0f}  ·  {r.recommendation}",
            expanded=(r.recommendation == "Buy"),
        ):
            st.markdown(rec_badge(r.recommendation), unsafe_allow_html=True)
            st.markdown("")

            left, right = st.columns([3, 2])
            with left:
                st.markdown(f"**Analysis:** {r.explanation}")
                if r.trade_plan:
                    tp = r.trade_plan
                    st.markdown("**Trade Plan:**")
                    t1, t2, t3, t4 = st.columns(4)
                    t1.metric("Entry",   f"${tp['entry']:.2f}")
                    t2.metric("Stop",    f"${tp['stop_loss']:.2f}")
                    t3.metric("Target",  f"${tp['take_profit']:.2f}")
                    t4.metric("Shares",  str(tp['position_size_shares']))
                    st.caption(r.risk_note)

            with right:
                st.markdown("**Factor Scores**")
                factor_items = [
                    ("Trend",         r.factor_scores.get("trend", 0)),
                    ("Momentum",      r.factor_scores.get("momentum", 0)),
                    ("Volume",        r.factor_scores.get("volume", 0)),
                    ("Entry Timing",  r.factor_scores.get("entry_timing", 0)),
                    ("Volatility",    r.factor_scores.get("volatility", 0)),
                    ("Multi-TF",      r.factor_scores.get("multi_timeframe", 0)),
                ]
                for label, score in factor_items:
                    c_l, c_r = st.columns([2, 1])
                    c_l.caption(label)
                    c_r.caption(f"{score:.0f}")
                    st.progress(int(min(100, max(0, score))))

    if st.session_state.last_scan_run:
        st.caption(f"Last scan at {st.session_state.last_scan_run}")


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3: MANAGE PORTFOLIO
# ─────────────────────────────────────────────────────────────────────────────

def render_manage_tab():
    st.header("✏️ Manage Portfolio")

    path      = st.session_state.portfolio_path
    portfolio = load_portfolio(path)

    # ── Current Holdings ─────────────────────────────────────────────────────
    st.subheader("Current Holdings")
    if not portfolio.holdings:
        st.info("No holdings yet. Add your first position below.")
    else:
        for h in portfolio.holdings:
            with st.expander(f"**{h.ticker}** — {h.shares} shares @ ${h.avg_cost:.2f}"):
                c1, c2, c3 = st.columns(3)
                c1.write(f"**Entry date:** {h.entry_date}")
                c2.write(f"**Trailing stop:** {h.trailing_stop_pct:.0%}")
                if h.stop_loss:
                    c3.write(f"**Hard stop:** ${h.stop_loss:.2f}")
                if h.take_profit:
                    c3.write(f"**Take profit:** ${h.take_profit:.2f}")
                if h.thesis:
                    st.caption(f"Thesis: *{h.thesis}*")

                if st.button(f"🗑️ Remove {h.ticker}", key=f"del_{h.ticker}"):
                    portfolio.remove_holding(h.ticker)
                    save_portfolio(portfolio, path)
                    st.success(f"{h.ticker} removed.")
                    st.rerun()

    # ── Add Holding Form ─────────────────────────────────────────────────────
    st.divider()
    st.subheader("Add New Holding")

    with st.form("add_holding_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        ticker    = c1.text_input("Ticker *",       placeholder="AAPL").upper().strip()
        shares    = c2.number_input("Shares *",     min_value=0.01, step=1.0, value=100.0)
        avg_cost  = c3.number_input("Avg Cost / Share *", min_value=0.01, step=0.01, value=100.00)

        c4, c5, c6 = st.columns(3)
        entry_date   = c4.date_input("Entry Date *", value=date.today())
        stop_loss    = c5.number_input("Hard Stop (optional)", min_value=0.0,  step=0.01, value=0.0)
        take_profit  = c6.number_input("Take Profit (optional)", min_value=0.0, step=0.01, value=0.0)

        c7, c8 = st.columns([3, 1])
        thesis          = c7.text_area("Thesis / Reason (optional)", height=80,
                                        placeholder="Why did you buy this stock?")
        trailing_stop   = c8.slider("Trailing Stop %", min_value=3, max_value=25, value=8) / 100.0

        submitted = st.form_submit_button("➕ Add Holding", type="primary")

    if submitted:
        if not ticker:
            st.error("Ticker is required.")
        elif shares <= 0 or avg_cost <= 0:
            st.error("Shares and average cost must be greater than zero.")
        else:
            new_holding = Holding(
                ticker=ticker,
                shares=float(shares),
                avg_cost=float(avg_cost),
                entry_date=entry_date.isoformat(),
                thesis=thesis.strip(),
                stop_loss=float(stop_loss) if stop_loss > 0 else None,
                take_profit=float(take_profit) if take_profit > 0 else None,
                peak_price=None,
                trailing_stop_pct=trailing_stop,
            )
            portfolio.add_holding(new_holding)
            save_portfolio(portfolio, path)
            st.success(f"✅ {ticker} added to portfolio. Refresh in the Portfolio tab to see analysis.")
            st.rerun()

    # ── Account Settings ─────────────────────────────────────────────────────
    st.divider()
    st.subheader("Account Settings")

    with st.form("account_form"):
        new_size = st.number_input(
            "Account Size ($)",
            min_value=1000.0,
            step=1000.0,
            value=portfolio.account_size,
            help="Total account value. Used for concentration risk checks and position sizing.",
        )
        if st.form_submit_button("💾 Save"):
            portfolio.account_size = float(new_size)
            save_portfolio(portfolio, path)
            st.success("Account size updated.")

    # ── Export / Import ───────────────────────────────────────────────────────
    st.divider()
    st.subheader("Export")
    with open(path, "r") as f:
        raw_json = f.read()

    st.download_button(
        label="⬇️ Download portfolio.json",
        data=raw_json,
        file_name="portfolio.json",
        mime="application/json",
    )

    if st.session_state.portfolio_reports:
        export_data = holding_reports_to_json(st.session_state.portfolio_reports)
        st.download_button(
            label="⬇️ Download last analysis (JSON)",
            data=json.dumps(export_data, indent=2),
            file_name="portfolio_analysis.json",
            mime="application/json",
        )


# ─────────────────────────────────────────────────────────────────────────────
# TAB 4: SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

def render_settings_tab():
    st.header("⚙️ Settings")
    st.caption(
        "These values are read from `config.py`. "
        "To permanently change them, edit that file and restart the app."
    )

    st.divider()
    st.subheader("Opportunity Scoring Weights")
    st.caption("How each factor contributes to the Buy/Watch/Avoid score (0–100).")

    fw = config.FACTOR_WEIGHTS
    rows = [
        {"Factor": k.replace("_", " ").title(), "Weight": f"{v:.0%}",
         "Meaning": _factor_meaning(k)}
        for k, v in fw.items()
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("Recommendation Bands")
    col1, col2 = st.columns(2)
    col1.markdown(f"""
| Score | Recommendation |
|-------|----------------|
| ≥ **{config.RECOMMENDATION_BANDS['Buy']}** | 🟢 Buy |
| ≥ **{config.RECOMMENDATION_BANDS['Watch']}** | 🟡 Watch |
| < **{config.RECOMMENDATION_BANDS['Watch']}** | ⚫ Avoid |
""")
    col2.markdown(f"""
| Sell Risk | Hold Recommendation |
|-----------|---------------------|
| ≥ **{config.SELL_RISK_THRESHOLDS['sell']}** | 🔴 Sell |
| ≥ **{config.SELL_RISK_THRESHOLDS['trim']}** | 🟠 Trim |
| ≥ **{config.SELL_RISK_THRESHOLDS['watch_closely']}** | 🟡 Watch Closely |
| < **{config.SELL_RISK_THRESHOLDS['watch_closely']}** | 🟢 Hold |
""")

    st.divider()
    st.subheader("Hard Filters (Auto-Avoid)")
    hf = config.HARD_FILTERS
    st.markdown(f"""
- **Minimum price:** ${hf['min_price']:.2f} — stocks below this are excluded (penny stocks)
- **Minimum daily volume:** {hf['min_avg_volume']:,} shares
- **Maximum ATR%:** {hf['max_atr_pct']:.0%} — excluded if daily range is too large to manage
""")

    st.divider()
    st.subheader("Risk Management")
    st.markdown(f"""
- **Risk per trade:** ${config.RISK_PER_TRADE_USD:,.0f}
- **Stop-loss multiplier:** {config.ATR_STOP_LOSS_MULTIPLIER}× ATR
- **Take-profit multiplier:** {config.ATR_TAKE_PROFIT_MULTIPLIER}× ATR
- **Default trailing stop:** {config.TRAILING_STOP_DEFAULT_PCT:.0%}
- **Concentration limit:** {config.MAX_POSITION_PCT:.0%} of portfolio per position
""")

    st.divider()
    st.subheader("Data Source")
    source = config.DATA_SOURCE
    if source != "taapi":
        st.markdown(
            f"**Active source:** `yfinance` — Yahoo Finance + local indicator computation. "
            f"Free, no API key required."
        )
    else:
        st.markdown(
            f"**Active source:** `taapi` — TAAPI.io API "
            f"({'key set ✓' if config.TAAPI_SECRET else '⚠️ no key set'})"
        )
    st.markdown(f"""
- **Primary timeframe:** `{config.INTERVALS['primary']}`
- **Secondary timeframe:** `{config.INTERVALS['secondary']}`
""")

    st.divider()
    st.info(
        "To change any of these values, open `config.py` in a text editor and restart the app. "
        "No Python knowledge required — every setting is labelled and explained in plain English."
    )


def _factor_meaning(key: str) -> str:
    meanings = {
        "trend":           "Price vs SMA/EMA + ADX strength",
        "momentum":        "MACD histogram + RSI sweet spot (40-65)",
        "volume":          "OBV direction proxy",
        "entry_timing":    "Bollinger Band position + Stochastic K",
        "volatility":      "ATR% inversely scaled",
        "multi_timeframe": "Daily vs 4h agreement",
        "risk_penalty":    "Max deduction for conflicts / missing data",
    }
    return meanings.get(key, "—")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    render_sidebar()

    tab_portfolio, tab_scanner, tab_manage, tab_settings = st.tabs([
        "📊 Portfolio",
        "🔍 Scanner",
        "✏️ Manage Portfolio",
        "⚙️ Settings",
    ])

    with tab_portfolio:
        render_portfolio_tab()

    with tab_scanner:
        render_scanner_tab()

    with tab_manage:
        render_manage_tab()

    with tab_settings:
        render_settings_tab()


if __name__ == "__main__":
    main()
