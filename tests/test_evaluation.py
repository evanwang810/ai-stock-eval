"""Tests for the evaluation harness (panel loading, metrics)."""

import json
from pathlib import Path

import pytest

from evaluation import panel as panel_mod
from evaluation import metrics
from evaluation.panel import Panel


def _write_day(dirp, date, rows):
    """rows: list of (ticker, price, blended, det, ai, pt)."""
    lines = []
    for tkr, px, bl, det, ai, pt in rows:
        lines.append(json.dumps({
            "ticker": tkr,
            "facts": {"price": px, "companyName": tkr + " Inc", "sector": "Tech"},
            "scores": {"blended": bl, "det_total": det, "ai_total": ai},
            "parsed": {"price_target": pt},
        }))
    (Path(dirp) / f"{date}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_panel_loads_signals_and_prices(tmp_path):
    _write_day(tmp_path, "2026-01-05",
               [(f"T{i}", 100 + i, i, i * 2, i * 3, 120 + i) for i in range(30)])
    p = panel_mod.load_panel(raw_dir=tmp_path)
    assert p.dates == ["2026-01-05"]
    assert len(p.price["2026-01-05"]) == 30
    assert p.signals["blended"]["2026-01-05"]["T5"] == 5
    assert p.signals["ai_total"]["2026-01-05"]["T5"] == 15
    assert p.ptarget["2026-01-05"]["T5"] == 125
    assert p.meta["T5"]["sector"] == "Tech"


def test_panel_drops_stale_duplicate_snapshot(tmp_path):
    base = [(f"T{i}", 100 + i, i, i, i, 120) for i in range(30)]
    _write_day(tmp_path, "2026-01-05", base)
    _write_day(tmp_path, "2026-01-06", base)                # identical -> dropped
    moved = [(f"T{i}", 110 + i, i, i, i, 120) for i in range(30)]
    _write_day(tmp_path, "2026-01-07", moved)               # real move -> kept
    p = panel_mod.load_panel(raw_dir=tmp_path)
    assert p.dates == ["2026-01-05", "2026-01-07"]
    assert p.dropped == ["2026-01-06"]


def test_panel_skips_bad_prices(tmp_path):
    _write_day(tmp_path, "2026-01-05",
               [("GOOD", 100, 5, 5, 5, 120), ("ZERO", 0, 5, 5, 5, 120),
                ("NEG", -3, 5, 5, 5, 120)] +
               [(f"T{i}", 100 + i, i, i, i, 120) for i in range(25)])
    p = panel_mod.load_panel(raw_dir=tmp_path)
    px = p.price["2026-01-05"]
    assert "GOOD" in px and "ZERO" not in px and "NEG" not in px


def test_spearman_monotonic():
    assert metrics.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert metrics.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    # non-linear but monotonic -> Spearman still 1 (Pearson wouldn't be)
    assert metrics.spearman([1, 2, 3, 4], [1, 4, 9, 16]) == pytest.approx(1.0)


def _panel_with_predictor(inverted=False):
    """Two days; on day0 the signal ranks exactly with day0->day1 returns."""
    n = 40
    d0, d1 = "2026-01-05", "2026-01-06"
    p0 = {f"T{i}": 100.0 for i in range(n)}
    # ticker i gains i% -> higher i = higher forward return
    p1 = {f"T{i}": 100.0 * (1 + i / 1000) for i in range(n)}
    sign = -1 if inverted else 1
    sig = {f"T{i}": float(sign * i) for i in range(n)}
    return Panel(
        dates=[d0, d1],
        price={d0: p0, d1: p1},
        signals={"blended": {d0: sig, d1: sig},
                 "det_total": {d0: sig, d1: sig},
                 "ai_total": {d0: sig, d1: sig}},
        ptarget={d0: {f"T{i}": 100.0 * (1 + i / 500) for i in range(n)}, d1: {}},
        meta={f"T{i}": {"companyName": f"T{i}", "sector": "Tech"} for i in range(n)},
    )


def test_rank_ic_detects_perfect_and_inverted_signal():
    good = metrics.rank_ic(_panel_with_predictor(), "blended", horizon=1)
    assert good["n_windows"] == 1
    assert good["mean_ic"] == pytest.approx(1.0)
    bad = metrics.rank_ic(_panel_with_predictor(inverted=True), "blended", horizon=1)
    assert bad["mean_ic"] == pytest.approx(-1.0)


def test_quantile_spread_and_topn_positive_for_good_signal():
    p = _panel_with_predictor()
    qs = metrics.quantile_spread(p, "blended", horizon=1, q=4)
    assert qs["spread"] > 0                    # top bucket beats bottom
    assert qs["bucket_means"] == sorted(qs["bucket_means"])   # monotonic
    tn = metrics.topn_long_only(p, "blended", horizon=1, n=10)
    assert tn["avg_excess"] > 0
    assert tn["beat_median_rate"] == pytest.approx(1.0)       # all top picks beat median


def test_pt_calibration_positive_corr():
    cal = metrics.pt_calibration(_panel_with_predictor(), horizon=1)
    assert cal["corr"] > 0.9                   # bigger target -> bigger realized


def test_forward_returns_drops_absurd():
    p = Panel(dates=["a", "b"],
              price={"a": {"X": 100, "Y": 100}, "b": {"X": 130, "Y": 100000}},
              signals={s: {"a": {}, "b": {}} for s in panel_mod.SIGNALS},
              ptarget={}, meta={})
    fr = metrics.forward_returns(p, "a", "b")
    assert "X" in fr and fr["X"] == pytest.approx(0.30)
    assert "Y" not in fr                        # 999x = data error, dropped


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
