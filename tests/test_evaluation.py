"""Tests for the evaluation harness (panel loading, metrics)."""

import json
from pathlib import Path

import pytest

from evaluation import panel as panel_mod


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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
