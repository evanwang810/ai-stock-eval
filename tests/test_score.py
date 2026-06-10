"""
test_score.py  ──  Regression tests for the scoring algorithm.

Run:  cd ai-stock-eval && py -m pytest tests/ -v
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from score import compute_score, DEFAULT_WEIGHTS


def score_total(facts, weights=None):
    return compute_score(facts, weights or DEFAULT_WEIGHTS)["total"]


class TestEdgeCases:
    def test_empty_facts_returns_zero(self):
        result = compute_score({}, DEFAULT_WEIGHTS)
        assert result["total"] == 0
        for v in result["breakdown"].values():
            assert v == 0

    def test_breakdown_keys_present(self):
        result = compute_score({"peTTM": 20}, DEFAULT_WEIGHTS)
        assert set(result["breakdown"]) == {
            "momentum", "valuation", "growth", "profitability", "risk", "technicals"
        }

    def test_total_within_bounds(self):
        extreme_bull = {
            "dayChangePct": 999, "weekTrendPct": 999, "oneMonthTrendPct": 999,
            "threeMonthTrendPct": 999, "yearTrendPct": 999,
            "peTTM": 5, "pb": 1.0, "epsTTM": 10, "debtToEquity": 0.1,
            "revenueGrowthPct": 99, "epsGrowthTTMYoy": 99,
            "netMarginPct": 40, "roeTTM": 40, "roic": 30, "grossMarginPct": 60,
            "beta": 0.4, "dividendYieldPct": 4, "currentRatio": 3,
            "distFrom52wHigh": -50, "distFrom52wLow": 30,
            "latestVolume": 1e7, "avgVolume10d": 1e6,
        }
        extreme_bear = {k: -abs(v) if isinstance(v, (int, float)) else v
                        for k, v in extreme_bull.items()}
        assert -1000 <= score_total(extreme_bull) <= 1000
        assert -1000 <= score_total(extreme_bear) <= 1000

    def test_custom_weights_scale_score(self):
        facts = {"dayChangePct": 5, "weekTrendPct": 3}
        w_default = score_total(facts)
        w_custom  = score_total(facts, {
            "momentum": 1.0, "valuation": 0, "growth": 0,
            "profitability": 0, "risk": 0, "technicals": 0,
        })
        assert w_custom != w_default


class TestMomentum:
    def test_strong_uptrend_positive(self):
        facts = {
            "dayChangePct": 3, "weekTrendPct": 5,
            "oneMonthTrendPct": 15, "threeMonthTrendPct": 30, "yearTrendPct": 60,
        }
        assert compute_score(facts, DEFAULT_WEIGHTS)["breakdown"]["momentum"] > 0

    def test_strong_downtrend_negative(self):
        facts = {
            "dayChangePct": -3, "weekTrendPct": -5,
            "oneMonthTrendPct": -15, "threeMonthTrendPct": -30, "yearTrendPct": -60,
        }
        assert compute_score(facts, DEFAULT_WEIGHTS)["breakdown"]["momentum"] < 0


class TestValuation:
    def test_low_pe_bullish(self):
        assert compute_score({"peTTM": 8}, DEFAULT_WEIGHTS)["breakdown"]["valuation"] > 0

    def test_negative_pe_bearish(self):
        assert compute_score({"peTTM": -5}, DEFAULT_WEIGHTS)["breakdown"]["valuation"] < 0

    def test_very_high_pe_bearish(self):
        assert compute_score({"peTTM": 100}, DEFAULT_WEIGHTS)["breakdown"]["valuation"] < 0

    def test_positive_eps_adds_points(self):
        with_eps    = compute_score({"epsTTM":  5}, DEFAULT_WEIGHTS)["breakdown"]["valuation"]
        without_eps = compute_score({},             DEFAULT_WEIGHTS)["breakdown"]["valuation"]
        assert with_eps > without_eps

    def test_negative_eps_subtracts_points(self):
        assert compute_score({"epsTTM": -5}, DEFAULT_WEIGHTS)["breakdown"]["valuation"] < 0


class TestGrowth:
    def test_high_revenue_growth_bullish(self):
        assert compute_score({"revenueGrowthPct": 50}, DEFAULT_WEIGHTS)["breakdown"]["growth"] > 0

    def test_negative_revenue_growth_bearish(self):
        assert compute_score({"revenueGrowthPct": -20}, DEFAULT_WEIGHTS)["breakdown"]["growth"] < 0

    def test_eps_growth_fallback_to_qtr(self):
        ttm         = compute_score({"epsGrowthTTMYoy": 35}, DEFAULT_WEIGHTS)["breakdown"]["growth"]
        qtr         = compute_score({"epsGrowthQtrYoy": 35}, DEFAULT_WEIGHTS)["breakdown"]["growth"]
        both_missing = compute_score({},                     DEFAULT_WEIGHTS)["breakdown"]["growth"]
        assert ttm > qtr
        assert qtr > both_missing


class TestProfitability:
    def test_high_margin_bullish(self):
        assert compute_score({"netMarginPct": 30}, DEFAULT_WEIGHTS)["breakdown"]["profitability"] > 0

    def test_negative_margin_bearish(self):
        assert compute_score({"netMarginPct": -5}, DEFAULT_WEIGHTS)["breakdown"]["profitability"] < 0

    def test_high_roic_adds_points(self):
        high = compute_score({"roic": 25}, DEFAULT_WEIGHTS)["breakdown"]["profitability"]
        low  = compute_score({"roic":  5}, DEFAULT_WEIGHTS)["breakdown"]["profitability"]
        assert high > low


class TestRisk:
    def test_low_beta_positive_risk_score(self):
        assert compute_score({"beta": 0.4}, DEFAULT_WEIGHTS)["breakdown"]["risk"] > 0

    def test_high_beta_negative_risk_score(self):
        assert compute_score({"beta": 2.5}, DEFAULT_WEIGHTS)["breakdown"]["risk"] < 0

    def test_dividend_yield_adds_points(self):
        with_div    = compute_score({"dividendYieldPct": 3.0}, DEFAULT_WEIGHTS)["breakdown"]["risk"]
        without_div = compute_score({},                        DEFAULT_WEIGHTS)["breakdown"]["risk"]
        assert with_div > without_div

    def test_near_52w_low_subtracts(self):
        near = compute_score({"distFrom52wLow":  5}, DEFAULT_WEIGHTS)["breakdown"]["risk"]
        far  = compute_score({"distFrom52wLow": 50}, DEFAULT_WEIGHTS)["breakdown"]["risk"]
        assert near < far


class TestTechnicals:
    def test_near_52w_high_bearish(self):
        assert compute_score({"distFrom52wHigh": -1}, DEFAULT_WEIGHTS)["breakdown"]["technicals"] < 0

    def test_far_below_52w_high_bullish(self):
        assert compute_score({"distFrom52wHigh": -50}, DEFAULT_WEIGHTS)["breakdown"]["technicals"] > 0

    def test_high_volume_ratio_adds_points(self):
        high = compute_score({"latestVolume": 3e6, "avgVolume10d": 1e6}, DEFAULT_WEIGHTS)["breakdown"]["technicals"]
        low  = compute_score({"latestVolume": 4e5, "avgVolume10d": 1e6}, DEFAULT_WEIGHTS)["breakdown"]["technicals"]
        assert high > low


SNAPSHOTS = [
    {
        "label": "AAPL-like: profitable, moderate growth, low beta",
        "facts": {
            "dayChangePct": 0.8, "weekTrendPct": 1.5, "oneMonthTrendPct": 4.0,
            "threeMonthTrendPct": 8.0, "yearTrendPct": 22.0,
            "peTTM": 28, "pb": 7.5, "epsTTM": 6.4, "debtToEquity": 1.8,
            "revenueGrowthPct": 8.0, "epsGrowthTTMYoy": 12.0,
            "netMarginPct": 25.0, "roeTTM": 160.0, "roic": 30.0, "grossMarginPct": 44.0,
            "beta": 1.2, "dividendYieldPct": 0.5, "currentRatio": 1.0,
            "distFrom52wHigh": -6.0, "distFrom52wLow": 18.0,
            "latestVolume": 6e7, "avgVolume10d": 5.5e7,
        },
        "expected_range": (50, 250),
    },
    {
        "label": "Value trap: cheap PE but shrinking revenue and high debt",
        "facts": {
            "dayChangePct": -0.5, "weekTrendPct": -1.0, "oneMonthTrendPct": -3.0,
            "threeMonthTrendPct": -8.0, "yearTrendPct": -15.0,
            "peTTM": 9, "pb": 1.2, "epsTTM": 2.0, "debtToEquity": 5.0,
            "revenueGrowthPct": -12.0, "epsGrowthTTMYoy": -20.0,
            "netMarginPct": 3.0, "roeTTM": 8.0, "roic": 4.0, "grossMarginPct": 18.0,
            "beta": 1.6, "dividendYieldPct": 4.0, "currentRatio": 0.7,
            "distFrom52wHigh": -18.0, "distFrom52wLow": 2.0,
            "latestVolume": 2e6, "avgVolume10d": 2e6,
        },
        # upper bound loosened from -100: facts without the newer indicators
        # (rsi14, SMAs, volatility) score slightly closer to zero by design
        "expected_range": (-500, -80),
    },
    {
        "label": "High-growth SaaS: high PE but strong acceleration",
        "facts": {
            "dayChangePct": 2.0, "weekTrendPct": 4.0, "oneMonthTrendPct": 12.0,
            "threeMonthTrendPct": 25.0, "yearTrendPct": 55.0,
            "peTTM": 65, "pb": 15.0, "epsTTM": 3.0, "debtToEquity": 0.3,
            "revenueGrowthPct": 35.0, "epsGrowthTTMYoy": 40.0,
            "netMarginPct": 18.0, "roeTTM": 22.0, "roic": 18.0, "grossMarginPct": 72.0,
            "beta": 1.4, "dividendYieldPct": 0.0, "currentRatio": 2.5,
            "distFrom52wHigh": -8.0, "distFrom52wLow": 60.0,
            "latestVolume": 3e6, "avgVolume10d": 2e6,
        },
        "expected_range": (100, 400),
    },
    {
        "label": "Stable utility: low beta, dividend, flat growth",
        "facts": {
            "dayChangePct": 0.1, "weekTrendPct": 0.3, "oneMonthTrendPct": 1.0,
            "threeMonthTrendPct": 2.0, "yearTrendPct": 5.0,
            "peTTM": 18, "pb": 1.8, "epsTTM": 3.5, "debtToEquity": 1.2,
            "revenueGrowthPct": 2.0, "epsGrowthTTMYoy": 3.0,
            "netMarginPct": 12.0, "roeTTM": 10.0, "roic": 8.0, "grossMarginPct": 35.0,
            "beta": 0.55, "dividendYieldPct": 3.5, "currentRatio": 1.3,
            "distFrom52wHigh": -12.0, "distFrom52wLow": 15.0,
            "latestVolume": 1e6, "avgVolume10d": 1e6,
        },
        "expected_range": (50, 250),
    },
]


@pytest.mark.parametrize("snap", SNAPSHOTS, ids=[s["label"] for s in SNAPSHOTS])
def test_snapshot(snap):
    result = compute_score(snap["facts"], DEFAULT_WEIGHTS)
    lo, hi = snap["expected_range"]
    assert lo <= result["total"] <= hi, (
        f'{snap["label"]}: got {result["total"]}, expected {lo}..{hi}\n'
        f'breakdown: {result["breakdown"]}'
    )
