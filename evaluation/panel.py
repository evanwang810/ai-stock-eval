"""
panel.py - load accumulated scans into aligned price / signal panels.

Each scan day is one data/raw/<date>.jsonl file (500-ish records). We pull the
scan-time price plus every signal we want to grade as a predictor, keyed by
(date, ticker). Forward returns come for free from later days' prices, so no
external market data is needed - the scans ARE the price history.

Two bits of real-world hygiene happen here:

  * stale snapshots - if a day's prices are almost identical to the previous
    kept day (a re-fetch that returned yesterday's numbers, or a weekend force
    run when markets were closed), that day is dropped. Otherwise it would add
    a fake ~0% forward window and drag every correlation toward zero.

  * junk prices - non-positive prices are skipped; absurd single-window returns
    (|ret| > MAX_RET, almost always a split or a bad tick) are dropped at
    return time, not here, so the raw price panel stays faithful.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT    = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"

# signals we grade as predictors of forward return. each maps to a per-record
# extractor. "blended" is the score the site actually ranks on; the other two
# let us horse-race whether the AI or the deterministic algo is pulling weight.
SIGNAL_PATHS = {
    "blended":   ("scores", "blended"),
    "det_total": ("scores", "det_total"),
    "ai_total":  ("scores", "ai_total"),
}
SIGNALS = tuple(SIGNAL_PATHS)

# a day whose prices are >= this fraction identical to the previous kept day is
# treated as a duplicate snapshot and dropped.
DUP_THRESHOLD = 0.90


def _dig(obj, path):
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


@dataclass
class Panel:
    """Aligned view of every scan, ready for cross-sectional eval."""
    dates:   list[str]                                  # kept scan dates, sorted
    price:   dict[str, dict[str, float]]                # date -> {ticker: price}
    signals: dict[str, dict[str, dict[str, float]]]     # sig -> date -> {tkr: val}
    ptarget: dict[str, dict[str, float]]                # date -> {ticker: target}
    meta:    dict[str, dict]                            # ticker -> {name, sector}
    dropped: list[str] = field(default_factory=list)    # dates dropped as dupes

    def tickers_on(self, date):
        return set(self.price.get(date, {}))

    def universe(self):
        u = set()
        for d in self.dates:
            u |= self.tickers_on(d)
        return u


def _load_day(path):
    """One raw file -> (price, {signal: vals}, ptarget, meta) for that date."""
    price, ptarget, meta = {}, {}, {}
    sig = {s: {} for s in SIGNALS}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            tkr = rec.get("ticker")
            facts = rec.get("facts") or {}
            px = facts.get("price")
            if not tkr or not isinstance(px, (int, float)) or px <= 0:
                continue
            price[tkr] = float(px)
            for s, p in SIGNAL_PATHS.items():
                v = _dig(rec, p)
                if isinstance(v, (int, float)):
                    sig[s][tkr] = float(v)
            pt = _dig(rec, ("parsed", "price_target"))
            if isinstance(pt, (int, float)) and pt > 0:
                ptarget[tkr] = float(pt)
            meta.setdefault(tkr, {
                "companyName": facts.get("companyName") or tkr,
                "sector":      facts.get("sector") or "",
            })
    return price, sig, ptarget, meta


def _is_duplicate(prices, prev_prices, threshold=DUP_THRESHOLD):
    """True if `prices` is a near-identical re-snapshot of `prev_prices`."""
    common = set(prices) & set(prev_prices)
    if len(common) < 20:
        return False
    same = sum(1 for t in common if prices[t] == prev_prices[t])
    return same / len(common) >= threshold


def load_panel(raw_dir=RAW_DIR, dup_threshold=DUP_THRESHOLD):
    """Read every data/raw/<date>.jsonl into a deduped Panel."""
    files = sorted(Path(raw_dir).glob("*.jsonl"))
    price, signals, ptarget, meta = {}, {s: {} for s in SIGNALS}, {}, {}
    kept, dropped = [], []
    prev_kept = None

    for f in files:
        date = f.stem
        day_px, day_sig, day_pt, day_meta = _load_day(f)
        if not day_px:
            continue
        if prev_kept is not None and _is_duplicate(day_px, price[prev_kept], dup_threshold):
            dropped.append(date)
            continue
        kept.append(date)
        price[date] = day_px
        for s in SIGNALS:
            signals[s][date] = day_sig[s]
        ptarget[date] = day_pt
        for t, m in day_meta.items():
            meta.setdefault(t, m)
        prev_kept = date

    return Panel(dates=kept, price=price, signals=signals,
                 ptarget=ptarget, meta=meta, dropped=dropped)
