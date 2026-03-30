"""
tests/test_scoring.py — Unit tests for the scoring engine.

Run with:
    pytest tests/ -v
    pytest tests/ -v --cov=. --cov-report=term-missing

WHAT WE TEST:
    • Indicator normalization functions (RSI, ADX, BB position, etc.)
    • Hard filter logic
    • Recommendation band mapping
    • Total score calculation (weight math + penalty deduction)
    • Risk management calculations (stop-loss, take-profit, position size)
    • Full pipeline smoke test using mock data

These tests use no real API calls and no mock data patches — they test
the pure mathematical logic of the scoring engine.
"""

import pytest
import sys
import os

# Add the project root to the path so imports work from the tests/ subdirectory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    parse_indicators,
    compute_mtf_score,
)
from scoring import (
    FactorScores,
    apply_hard_filters,
    compute_total_score,
    get_recommendation,
    compute_confidence,
    score_ticker,
)
from risk import (
    compute_stop_loss,
    compute_take_profit,
    compute_position_size,
    build_trade_plan,
)
from taapi_client import RawIndicatorBundle


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def make_raw_bundle(ticker="TEST", interval="1d", **kwargs) -> RawIndicatorBundle:
    """Create a RawIndicatorBundle with sensible defaults."""
    defaults = dict(
        rsi=55.0,
        macd_value=0.50, macd_signal=0.30, macd_histogram=0.20,
        sma_20=98.0, ema_20=98.5,
        bb_upper=105.0, bb_middle=100.0, bb_lower=95.0,
        stoch_k=55.0, stoch_d=52.0,
        atr=2.0, adx=25.0,
        obv=1_000_000,
        close=100.0, volume=600_000,
    )
    defaults.update(kwargs)
    return RawIndicatorBundle(ticker=ticker, interval=interval, **defaults)


def make_indicator_data(ticker="TEST", interval="1d", **kwargs) -> IndicatorData:
    """Create an IndicatorData by parsing a RawIndicatorBundle with overrides."""
    raw = make_raw_bundle(ticker=ticker, interval=interval, **kwargs)
    return parse_indicators(raw)


# ─────────────────────────────────────────────────────────────────────────────
# RSI NORMALIZATION
# ─────────────────────────────────────────────────────────────────────────────

class TestRsiNormalization:
    """
    RSI sweet spot is 40-65.
    Ideal zone → 100. Outside zone → penalty. None → 50 (neutral).
    """

    def test_ideal_zone_centre_returns_100(self):
        assert normalize_rsi(52.0) == 100.0

    def test_ideal_zone_lower_bound_returns_100(self):
        assert normalize_rsi(config.RSI_IDEAL_LOW) == 100.0

    def test_ideal_zone_upper_bound_returns_100(self):
        assert normalize_rsi(config.RSI_IDEAL_HIGH) == 100.0

    def test_none_returns_neutral_50(self):
        assert normalize_rsi(None) == 50.0

    def test_overbought_scores_lower_than_ideal(self):
        score_in_ideal = normalize_rsi(55.0)
        score_overbought = normalize_rsi(80.0)
        assert score_overbought < score_in_ideal

    def test_extreme_overbought_is_very_low(self):
        # RSI = 95 should be near 0
        score = normalize_rsi(95.0)
        assert score < 10.0

    def test_oversold_penalized(self):
        score_ideal = normalize_rsi(50.0)
        score_oversold = normalize_rsi(20.0)
        assert score_oversold < score_ideal

    def test_score_always_0_to_100(self):
        """Score must always be in valid range for any RSI value 0-100."""
        for rsi_val in range(0, 101, 5):
            score = normalize_rsi(float(rsi_val))
            assert 0.0 <= score <= 100.0, f"RSI={rsi_val} produced out-of-range score {score}"

    def test_rsi_75_below_40_threshold(self):
        # RSI 75 is at the overbought boundary — should be below 40
        score = normalize_rsi(config.RSI_OVERBOUGHT)
        assert score <= 40.0

    def test_approaching_from_below_ramps_up(self):
        # RSI 35 should score higher than RSI 25
        assert normalize_rsi(35.0) > normalize_rsi(25.0)

    def test_approaching_from_above_ramps_down(self):
        # RSI 70 should score lower than RSI 60
        assert normalize_rsi(70.0) < normalize_rsi(60.0)


# ─────────────────────────────────────────────────────────────────────────────
# ADX NORMALIZATION
# ─────────────────────────────────────────────────────────────────────────────

class TestAdxNormalization:
    """
    ADX measures trend strength. Higher ADX = higher score.
    ADX ≥ 40 = 100. ADX < 15 = very low. None = 50 (neutral).
    """

    def test_very_strong_trend_returns_100(self):
        assert normalize_adx(40.0) == 100.0

    def test_above_very_strong_also_returns_100(self):
        assert normalize_adx(55.0) == 100.0

    def test_strong_trend_threshold_returns_60(self):
        # ADX exactly at strong threshold (25) should return 60
        score = normalize_adx(config.ADX_STRONG)
        assert abs(score - 60.0) < 1.0

    def test_weak_trend_threshold_returns_20(self):
        # ADX exactly at weak threshold (15) should return 20
        score = normalize_adx(config.ADX_WEAK)
        assert abs(score - 20.0) < 1.0

    def test_zero_adx_returns_zero(self):
        assert normalize_adx(0.0) == 0.0

    def test_none_returns_neutral_50(self):
        assert normalize_adx(None) == 50.0

    def test_monotonically_increasing(self):
        """Higher ADX should always produce higher or equal score."""
        scores = [normalize_adx(float(v)) for v in range(0, 51, 5)]
        for i in range(len(scores) - 1):
            assert scores[i] <= scores[i + 1], (
                f"ADX score not monotonic at {i*5} → {(i+1)*5}: {scores[i]} > {scores[i+1]}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# BOLLINGER BAND POSITION
# ─────────────────────────────────────────────────────────────────────────────

class TestBollingerBandPosition:
    """
    BB position 0.3-0.5 = ideal = 100.
    Near upper band = penalty. Above upper band = 0.
    None inputs = 50 (neutral).
    """

    def test_ideal_zone_lower_returns_100(self):
        # close = 182, upper = 191, lower = 179 → bb_pct = (182-179)/(191-179) = 3/12 = 0.25 ... not ideal
        # Need bb_pct = 0.4: close = 179 + 0.4*(191-179) = 179 + 4.8 = 183.8
        score = normalize_bb_position(183.8, 191.0, 179.0)
        assert score == 100.0

    def test_middle_band_returns_100(self):
        # close at bb_pct = 0.5 (middle) → score 100
        # close = 179 + 0.5 * 12 = 185
        score = normalize_bb_position(185.0, 191.0, 179.0)
        assert score == 100.0

    def test_above_upper_band_returns_0(self):
        score = normalize_bb_position(195.0, 191.0, 179.0)
        assert score == 0.0

    def test_near_upper_band_is_penalized(self):
        # bb_pct = 0.9: close = 179 + 0.9 * 12 = 189.8
        score_ideal = normalize_bb_position(185.0, 191.0, 179.0)
        score_extended = normalize_bb_position(189.8, 191.0, 179.0)
        assert score_extended < score_ideal

    def test_none_close_returns_50(self):
        assert normalize_bb_position(None, 191.0, 179.0) == 50.0

    def test_none_bands_return_50(self):
        assert normalize_bb_position(185.0, None, 179.0) == 50.0

    def test_near_lower_band_is_acceptable(self):
        # bb_pct = 0.1 (near lower band in uptrend = pullback opportunity)
        # close = 179 + 0.1 * 12 = 180.2
        score = normalize_bb_position(180.2, 191.0, 179.0)
        assert score > 0  # not ideal but not penalized to 0


# ─────────────────────────────────────────────────────────────────────────────
# ATR PERCENTAGE NORMALIZATION
# ─────────────────────────────────────────────────────────────────────────────

class TestAtrPctNormalization:
    """
    ATR% inversely scaled — lower volatility = higher score.
    ≤ 1.5% → 100. 6%+ → 0.
    """

    def test_very_low_atr_returns_100(self):
        assert normalize_atr_pct(0.01) == 100.0   # 1% ATR → perfect

    def test_at_ideal_threshold_returns_100(self):
        assert normalize_atr_pct(config.ATR_PCT_IDEAL) == 100.0  # exactly 1.5%

    def test_high_atr_returns_zero(self):
        assert normalize_atr_pct(config.ATR_PCT_MAX) == 0.0   # 6% → 0

    def test_above_max_atr_returns_zero(self):
        assert normalize_atr_pct(0.09) == 0.0   # 9% → hard filter territory

    def test_none_returns_neutral_50(self):
        assert normalize_atr_pct(None) == 50.0

    def test_inversely_scaled(self):
        """Higher ATR% must produce lower or equal score."""
        atr_values = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07]
        scores = [normalize_atr_pct(v) for v in atr_values]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], (
                f"ATR% not inversely scaled: {atr_values[i]:.0%} score "
                f"{scores[i]} > {atr_values[i+1]:.0%} score {scores[i+1]}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# HARD FILTERS
# ─────────────────────────────────────────────────────────────────────────────

class TestHardFilters:
    """
    Hard filters are non-negotiable disqualifiers.
    Any failure → filters_passed = False → recommendation = "Avoid".
    """

    def test_all_filters_pass_with_good_data(self):
        data = make_indicator_data(close=50.0, volume=1_000_000, atr=1.0)
        passed, failures = apply_hard_filters(data)
        assert passed is True
        assert failures == []

    def test_price_below_minimum_fails(self):
        data = make_indicator_data(close=3.50, volume=1_000_000, atr=0.1)
        passed, failures = apply_hard_filters(data)
        assert passed is False
        assert any("price_below_minimum" in f for f in failures)

    def test_price_exactly_at_minimum_passes(self):
        data = make_indicator_data(close=config.HARD_FILTERS["min_price"], volume=1_000_000, atr=0.1)
        passed, failures = apply_hard_filters(data)
        # At exactly the threshold: close < min_price is False → passes
        assert passed is True

    def test_volume_below_minimum_fails(self):
        data = make_indicator_data(close=100.0, volume=100_000, atr=1.0)
        passed, failures = apply_hard_filters(data)
        assert passed is False
        assert any("volume_below_minimum" in f for f in failures)

    def test_atr_too_high_fails(self):
        # ATR = 9% of price: close=100, atr=9 → atr_pct = 0.09 > 0.08
        data = make_indicator_data(close=100.0, volume=1_000_000, atr=9.0)
        passed, failures = apply_hard_filters(data)
        assert passed is False
        assert any("atr_too_high" in f for f in failures)

    def test_multiple_failures_reported(self):
        # Both price and volume fail
        data = make_indicator_data(close=2.0, volume=50_000, atr=0.1)
        passed, failures = apply_hard_filters(data)
        assert passed is False
        assert len(failures) >= 2

    def test_missing_close_price_fails(self):
        data = make_indicator_data()
        data.close = None      # Manually clear the close price
        data.atr_pct = None    # Derived field also cleared
        passed, failures = apply_hard_filters(data)
        assert passed is False
        assert any("missing_close_price" in f for f in failures)


# ─────────────────────────────────────────────────────────────────────────────
# RECOMMENDATION MAPPING
# ─────────────────────────────────────────────────────────────────────────────

class TestRecommendationMapping:
    """
    Score >= 80 → Buy. Score >= 60 → Watch. Score < 60 → Avoid.
    Filter failure always → Avoid regardless of score.
    """

    def test_score_85_is_buy(self):
        assert get_recommendation(85.0, True) == "Buy"

    def test_score_80_boundary_is_buy(self):
        assert get_recommendation(80.0, True) == "Buy"

    def test_score_79_is_watch(self):
        assert get_recommendation(79.0, True) == "Watch"

    def test_score_70_is_watch(self):
        assert get_recommendation(70.0, True) == "Watch"

    def test_score_60_boundary_is_watch(self):
        assert get_recommendation(60.0, True) == "Watch"

    def test_score_59_is_avoid(self):
        assert get_recommendation(59.9, True) == "Avoid"

    def test_score_45_is_avoid(self):
        assert get_recommendation(45.0, True) == "Avoid"

    def test_filter_failure_forces_avoid_even_with_high_score(self):
        # Even a "perfect" score is overridden by a hard filter failure
        assert get_recommendation(95.0, False) == "Avoid"

    def test_filter_failure_with_low_score_also_avoid(self):
        assert get_recommendation(30.0, False) == "Avoid"

    def test_score_0_is_avoid(self):
        assert get_recommendation(0.0, True) == "Avoid"

    def test_score_100_is_buy(self):
        assert get_recommendation(100.0, True) == "Buy"


# ─────────────────────────────────────────────────────────────────────────────
# TOTAL SCORE CALCULATION
# ─────────────────────────────────────────────────────────────────────────────

class TestTotalScoreCalculation:
    """
    Test the weighted sum formula and penalty deduction math.
    """

    def test_all_perfect_factor_scores_returns_near_100(self):
        scores = FactorScores(
            trend=100, momentum=100, volume=100,
            entry_timing=100, volatility=100,
            multi_timeframe=100, risk_penalty=0.0,
        )
        raw, final = compute_total_score(scores)
        # All factor weights (excluding penalty) sum to 0.95
        # 100 * 0.95 = 95 (penalty weight 0.05 × 0 = 0 deduction)
        assert final == pytest.approx(95.0, abs=0.1)

    def test_all_zero_scores_returns_zero(self):
        scores = FactorScores(
            trend=0, momentum=0, volume=0,
            entry_timing=0, volatility=0,
            multi_timeframe=0, risk_penalty=0.0,
        )
        raw, final = compute_total_score(scores)
        assert final == 0.0

    def test_max_penalty_deducts_5_points(self):
        """
        Maximum penalty = risk_penalty score of 100 × weight of 0.05 = 5.0 points.
        """
        no_penalty = FactorScores(
            trend=100, momentum=100, volume=100,
            entry_timing=100, volatility=100,
            multi_timeframe=100, risk_penalty=0.0,
        )
        max_penalty = FactorScores(
            trend=100, momentum=100, volume=100,
            entry_timing=100, volatility=100,
            multi_timeframe=100, risk_penalty=100.0,
        )
        _, score_no_pen = compute_total_score(no_penalty)
        _, score_max_pen = compute_total_score(max_penalty)
        assert score_no_pen - score_max_pen == pytest.approx(5.0, abs=0.01)

    def test_score_clamped_to_100(self):
        """Score cannot exceed 100."""
        scores = FactorScores(
            trend=100, momentum=100, volume=100,
            entry_timing=100, volatility=100,
            multi_timeframe=100, risk_penalty=0.0,
        )
        _, final = compute_total_score(scores)
        assert final <= 100.0

    def test_score_clamped_to_0(self):
        """Score cannot go negative."""
        scores = FactorScores(
            trend=0, momentum=0, volume=0,
            entry_timing=0, volatility=0,
            multi_timeframe=0, risk_penalty=100.0,
        )
        _, final = compute_total_score(scores)
        assert final >= 0.0

    def test_weights_applied_correctly(self):
        """
        Test a specific combination to verify weights are applied correctly.
        trend=100 (weight 0.25) with all others at 0 should produce raw=25.
        """
        scores = FactorScores(
            trend=100, momentum=0, volume=0,
            entry_timing=0, volatility=0,
            multi_timeframe=0, risk_penalty=0.0,
        )
        raw, _ = compute_total_score(scores)
        assert raw == pytest.approx(
            100 * config.FACTOR_WEIGHTS["trend"], abs=0.01
        )


# ─────────────────────────────────────────────────────────────────────────────
# RISK CALCULATIONS
# ─────────────────────────────────────────────────────────────────────────────

class TestRiskCalculations:
    """
    Test stop-loss, take-profit, and position sizing math.
    """

    def test_stop_loss_is_below_entry(self):
        stop = compute_stop_loss(close=100.0, atr=2.0, atr_multiplier=1.5)
        assert stop < 100.0

    def test_stop_loss_formula(self):
        # stop = close - (atr × multiplier) = 100 - (2.0 × 1.5) = 97.0
        stop = compute_stop_loss(close=100.0, atr=2.0, atr_multiplier=1.5)
        assert stop == pytest.approx(97.0, abs=0.01)

    def test_stop_loss_uses_config_multiplier_by_default(self):
        stop_default = compute_stop_loss(close=100.0, atr=2.0)
        stop_explicit = compute_stop_loss(close=100.0, atr=2.0, atr_multiplier=config.ATR_STOP_LOSS_MULTIPLIER)
        assert stop_default == stop_explicit

    def test_take_profit_is_above_entry(self):
        stop = compute_stop_loss(close=100.0, atr=2.0, atr_multiplier=1.5)
        tp = compute_take_profit(close=100.0, stop_loss=stop, reward_risk_ratio=2.5)
        assert tp > 100.0

    def test_take_profit_formula(self):
        # close=100, stop=97 → stop_dist=3 → tp = 100 + (3 × 2.5) = 107.50
        stop = 97.0
        tp = compute_take_profit(close=100.0, stop_loss=stop, reward_risk_ratio=2.5)
        assert tp == pytest.approx(107.5, abs=0.01)

    def test_take_profit_reward_risk_ratio(self):
        # With ratio=2.5: reward = 2.5 × risk
        entry = 100.0
        stop = 97.0
        tp = compute_take_profit(close=entry, stop_loss=stop, reward_risk_ratio=2.5)
        risk   = entry - stop   # = 3.0
        reward = tp - entry      # = 7.5
        assert reward / risk == pytest.approx(2.5, abs=0.01)

    def test_position_size_respects_risk_budget(self):
        # risk/share = 3.0, budget = 500 → 166 shares, actual risk ≤ 500
        shares, actual_risk = compute_position_size(
            close=100.0, stop_loss=97.0, risk_per_trade_usd=500.0
        )
        assert shares == 166
        assert actual_risk <= 500.0
        assert actual_risk == pytest.approx(166 * 3.0, abs=0.01)

    def test_position_size_never_exceeds_budget(self):
        for risk_budget in [100, 250, 500, 1000]:
            shares, actual_risk = compute_position_size(
                close=50.0, stop_loss=47.5, risk_per_trade_usd=float(risk_budget)
            )
            assert actual_risk <= risk_budget + 0.01  # tiny tolerance for float

    def test_invalid_stop_above_entry_returns_zero_shares(self):
        shares, actual_risk = compute_position_size(
            close=100.0, stop_loss=105.0, risk_per_trade_usd=500.0
        )
        assert shares == 0
        assert actual_risk == 0.0

    def test_build_trade_plan_returns_none_for_filter_failure(self):
        data = make_indicator_data(close=2.0, volume=50_000, atr=0.5)
        plan = build_trade_plan(data, score=30.0, filters_passed=False)
        assert plan is None

    def test_build_trade_plan_returns_plan_for_valid_data(self):
        data = make_indicator_data(close=100.0, volume=1_000_000, atr=2.0)
        plan = build_trade_plan(data, score=80.0, filters_passed=True)
        assert plan is not None
        assert plan.stop_loss < plan.entry < plan.take_profit
        assert plan.position_size_shares > 0
        assert plan.risk_amount_usd > 0

    def test_build_trade_plan_entry_equals_close(self):
        data = make_indicator_data(close=150.0, volume=1_000_000, atr=3.0)
        plan = build_trade_plan(data, score=75.0, filters_passed=True)
        assert plan is not None
        assert plan.entry == 150.0


# ─────────────────────────────────────────────────────────────────────────────
# MULTI-TIMEFRAME SCORE
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiTimeframeScore:
    """
    MTF score rewards agreement between daily and 4h signals.
    No 4h data → neutral score of 50.
    Full agreement → 100. No agreement → 0.
    """

    def test_no_h4_data_returns_50(self):
        daily = make_indicator_data()
        score = compute_mtf_score(daily, h4=None)
        assert score == 50.0

    def test_full_agreement_returns_100(self):
        # Both timeframes: RSI > 50, MACD histogram positive, price > SMA
        daily = make_indicator_data(rsi=60.0, macd_histogram=0.5, close=105.0, sma_20=100.0)
        h4    = make_indicator_data(rsi=58.0, macd_histogram=0.3, close=105.0, sma_20=100.0)
        score = compute_mtf_score(daily, h4)
        assert score == 100.0

    def test_full_disagreement_returns_0(self):
        # Daily: RSI > 50, MACD positive, price above SMA
        # 4h: RSI < 50, MACD negative, price below SMA
        daily = make_indicator_data(rsi=60.0, macd_histogram=0.5, close=105.0, sma_20=100.0)
        h4    = make_indicator_data(rsi=40.0, macd_histogram=-0.3, close=95.0, sma_20=100.0)
        score = compute_mtf_score(daily, h4)
        assert score == 0.0

    def test_partial_agreement_between_0_and_100(self):
        # 2/3 checks agree → score = 66.7
        daily = make_indicator_data(rsi=60.0, macd_histogram=0.5, close=105.0, sma_20=100.0)
        h4    = make_indicator_data(rsi=58.0, macd_histogram=-0.3, close=105.0, sma_20=100.0)
        score = compute_mtf_score(daily, h4)
        assert 0.0 < score < 100.0


# ─────────────────────────────────────────────────────────────────────────────
# FULL PIPELINE SMOKE TEST (mock data)
# ─────────────────────────────────────────────────────────────────────────────

class TestFullPipelineSmoke:
    """
    End-to-end smoke tests using the mock data defined in taapi_client.py.
    These verify the whole pipeline runs without errors and produces
    sensible results for the five pre-defined scenarios.
    """

    def setup_method(self):
        """Force mock mode for all tests in this class."""
        config.MOCK_MODE = True

    def teardown_method(self):
        """Reset mock mode after each test."""
        config.MOCK_MODE = False

    def test_aapl_mock_is_buy(self):
        """AAPL mock data represents a strong buy setup — should score >= 80."""
        from taapi_client import _fetch_mock
        from indicators import parse_indicators

        daily_raw = _fetch_mock("AAPL", "1d")
        h4_raw    = _fetch_mock("AAPL", "4h")
        daily = parse_indicators(daily_raw)
        h4    = parse_indicators(h4_raw)
        result = score_ticker("AAPL", daily, h4)

        assert result.recommendation == "Buy", (
            f"Expected AAPL to be Buy, got {result.recommendation} (score={result.final_score})"
        )
        assert result.final_score >= 75.0, (
            f"Expected AAPL score >= 75, got {result.final_score}"
        )
        assert result.filters_passed is True

    def test_sndl_mock_is_auto_avoid(self):
        """SNDL mock has price < $5 — must be filtered to Avoid before scoring."""
        from taapi_client import _fetch_mock
        from indicators import parse_indicators

        daily_raw = _fetch_mock("SNDL", "1d")
        daily = parse_indicators(daily_raw)
        result = score_ticker("SNDL", daily)

        assert result.recommendation == "Avoid"
        assert result.filters_passed is False
        assert any("price" in f for f in result.filter_failures)

    def test_amc_mock_is_avoid(self):
        """AMC mock data represents a weak/bearish setup — should be Avoid."""
        from taapi_client import _fetch_mock
        from indicators import parse_indicators

        daily_raw = _fetch_mock("AMC", "1d")
        h4_raw    = _fetch_mock("AMC", "4h")
        daily = parse_indicators(daily_raw)
        h4    = parse_indicators(h4_raw)
        result = score_ticker("AMC", daily, h4)

        assert result.recommendation == "Avoid", (
            f"Expected AMC to be Avoid, got {result.recommendation} (score={result.final_score})"
        )

    def test_score_is_bounded(self):
        """All mock tickers must produce scores in 0-100 range."""
        from taapi_client import _fetch_mock
        from indicators import parse_indicators

        for ticker in ["AAPL", "MSFT", "NVDA", "AMC", "SNDL"]:
            daily_raw = _fetch_mock(ticker, "1d")
            daily = parse_indicators(daily_raw)
            result = score_ticker(ticker, daily)
            assert 0.0 <= result.final_score <= 100.0, (
                f"{ticker}: score {result.final_score} is out of 0-100 range"
            )

    def test_trade_plan_generated_for_buy(self):
        """A Buy recommendation should always have a trade plan."""
        from taapi_client import _fetch_mock
        from indicators import parse_indicators
        from risk import build_trade_plan

        daily_raw = _fetch_mock("AAPL", "1d")
        daily = parse_indicators(daily_raw)
        result = score_ticker("AAPL", daily)
        plan = build_trade_plan(daily, result.final_score, result.filters_passed)

        assert plan is not None
        assert plan.stop_loss < plan.entry
        assert plan.take_profit > plan.entry
        assert plan.position_size_shares > 0

    def test_explanation_is_non_empty_string(self):
        """Every result must have a non-empty explanation."""
        from taapi_client import _fetch_mock
        from indicators import parse_indicators

        for ticker in ["AAPL", "MSFT", "SNDL"]:
            daily_raw = _fetch_mock(ticker, "1d")
            daily = parse_indicators(daily_raw)
            result = score_ticker(ticker, daily)
            assert isinstance(result.explanation, str)
            assert len(result.explanation) > 20, (
                f"{ticker}: explanation too short: '{result.explanation}'"
            )

    def test_ranking_order_by_score(self):
        """run_engine should return stocks sorted best-first."""
        from engine import run_engine
        reports = run_engine(["AAPL", "MSFT", "NVDA", "AMC", "SNDL"])
        scores = [r.score for r in reports]
        assert scores == sorted(scores, reverse=True), (
            f"Reports not sorted by score descending: {scores}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# MA ALIGNMENT NORMALIZATION
# ─────────────────────────────────────────────────────────────────────────────

class TestMaAlignmentNormalization:
    """
    Price above both MAs → high score (70-100).
    Price below both MAs → low score (0-30).
    Mixed → 50.
    """

    def test_price_above_both_mas_returns_high_score(self):
        score = normalize_ma_alignment(105.0, 100.0, 99.0)
        assert score >= 70.0

    def test_price_below_both_mas_returns_low_score(self):
        score = normalize_ma_alignment(95.0, 100.0, 99.0)
        assert score <= 30.0

    def test_price_between_mas_returns_50(self):
        # close = 100, sma = 98, ema = 102 → price above sma, below ema
        score = normalize_ma_alignment(100.0, 98.0, 102.0)
        assert score == 50.0

    def test_none_close_returns_50(self):
        assert normalize_ma_alignment(None, 100.0, 99.0) == 50.0

    def test_no_ma_data_returns_50(self):
        assert normalize_ma_alignment(105.0, None, None) == 50.0
