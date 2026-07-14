"""
metrics.py - does the score predict forward returns?

Everything here is cross-sectional: on each scan day we rank the universe by a
signal, then check how those ranks lined up with what the stocks actually did
over the next `horizon` scans. Pure stdlib, no numpy.

The headline numbers:

  rank_ic         - Spearman correlation between signal and forward return,
                    averaged over windows. This is the "information coefficient"
                    quants live by. ~0 = no skill; 0.05+ sustained = real edge.
  quantile_spread - bucket each day by signal, average each bucket's forward
                    return. A working signal shows the top bucket beating the
                    bottom (monotonic ideally).
  topn_long_only  - the realistic version: mean return of the top-N picks minus
                    the universe, since you can rank-and-buy but can't easily
                    short the bottom.
  pt_calibration  - do bigger AI price targets actually precede bigger moves?

Windows overlap (a 3-day IC on daily scans reuses days), so treat the window
count as optimistic - the caveats live in report.py.
"""

from __future__ import annotations

# a single-window move past this is almost always a split or bad tick, not a
# real return - drop it rather than let it dominate a mean.
MAX_RET = 1.0


# ── rank helpers ────────────────────────────────────────────────────────────

def _ranks(vals):
    """1-based ranks with ties averaged."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(a, b):
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return cov / (va * vb) ** 0.5


def spearman(xs, ys):
    if len(xs) < 3:
        return None
    return _pearson(_ranks(xs), _ranks(ys))


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _stdev(xs):
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    return (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5


# ── forward returns ─────────────────────────────────────────────────────────

def forward_returns(panel, d0, d1, max_ret=MAX_RET):
    """{ticker: pct return} from d0 to d1 for tickers priced on both days."""
    p0, p1 = panel.price.get(d0, {}), panel.price.get(d1, {})
    out = {}
    for t in set(p0) & set(p1):
        if p0[t] > 0:
            r = p1[t] / p0[t] - 1
            if abs(r) <= max_ret:
                out[t] = r
    return out


def _windows(panel, horizon):
    """Yield (d0, d1) pairs `horizon` scans apart."""
    d = panel.dates
    for i in range(len(d) - horizon):
        yield d[i], d[i + horizon]


# ── metrics ─────────────────────────────────────────────────────────────────

def rank_ic(panel, signal, horizon, max_ret=MAX_RET, min_names=10):
    """Mean Spearman IC of `signal` vs forward return over all windows."""
    sig = panel.signals[signal]
    per = []
    for d0, d1 in _windows(panel, horizon):
        fr = forward_returns(panel, d0, d1, max_ret)
        s0 = sig.get(d0, {})
        common = [t for t in fr if t in s0]
        if len(common) < min_names:
            continue
        ic = spearman([s0[t] for t in common], [fr[t] for t in common])
        if ic is not None:
            per.append({"from": d0, "to": d1, "ic": ic, "n": len(common)})
    ics = [p["ic"] for p in per]
    mean = _mean(ics)
    sd = _stdev(ics)
    tstat = (mean / (sd / len(ics) ** 0.5)) if (mean is not None and sd) else None
    return {
        "signal": signal, "horizon": horizon,
        "n_windows": len(ics), "mean_ic": mean,
        "hit_rate": (_mean([1.0 if i > 0 else 0.0 for i in ics]) if ics else None),
        "tstat": tstat, "per_window": per,
    }


def quantile_spread(panel, signal, horizon, q=5, max_ret=MAX_RET):
    """Bucket each day by signal; mean forward return per bucket (pooled)."""
    sig = panel.signals[signal]
    buckets = [[] for _ in range(q)]
    allrets = []
    for d0, d1 in _windows(panel, horizon):
        fr = forward_returns(panel, d0, d1, max_ret)
        s0 = sig.get(d0, {})
        common = [t for t in fr if t in s0]
        if len(common) < q * 3:
            continue
        common.sort(key=lambda t: s0[t])          # ascending -> bucket 0 = worst
        n = len(common)
        for idx, t in enumerate(common):
            b = min(q - 1, idx * q // n)
            buckets[b].append(fr[t])
            allrets.append(fr[t])
    means = [_mean(b) for b in buckets]
    top, bottom, overall = means[-1], means[0], _mean(allrets)
    return {
        "signal": signal, "horizon": horizon, "q": q,
        "bucket_means": means, "bucket_counts": [len(b) for b in buckets],
        "top": top, "bottom": bottom,
        "spread": (top - bottom) if (top is not None and bottom is not None) else None,
        "long_only_excess": (top - overall) if (top is not None and overall is not None) else None,
        "universe_mean": overall,
    }


def topn_long_only(panel, signal, horizon, n=20, max_ret=MAX_RET):
    """Top-N picks by signal vs the universe - the buy-and-hold-the-top view."""
    sig = panel.signals[signal]
    excess, pick_r, uni_r = [], [], []
    hit, tot = 0, 0
    for d0, d1 in _windows(panel, horizon):
        fr = forward_returns(panel, d0, d1, max_ret)
        s0 = sig.get(d0, {})
        common = [t for t in fr if t in s0]
        if len(common) < n * 2:
            continue
        common.sort(key=lambda t: s0[t], reverse=True)
        top = common[:n]
        uni_mean = _mean([fr[t] for t in common])
        srt = sorted(fr[t] for t in common)
        med = srt[len(srt) // 2]
        tavg = _mean([fr[t] for t in top])
        excess.append(tavg - uni_mean)
        pick_r.append(tavg)
        uni_r.append(uni_mean)
        for t in top:
            tot += 1
            hit += 1 if fr[t] > med else 0
    return {
        "signal": signal, "horizon": horizon, "n": n,
        "n_windows": len(excess),
        "avg_excess": _mean(excess),
        "avg_pick_return": _mean(pick_r),
        "avg_universe_return": _mean(uni_r),
        "beat_median_rate": (hit / tot) if tot else None,
    }


# implied-upside bands for the price-target check
_PT_BANDS = [(-1.0, 0.0), (0.0, 0.05), (0.05, 0.15), (0.15, 0.30), (0.30, 10.0)]
_PT_LABELS = ["<0%", "0-5%", "5-15%", "15-30%", "30%+"]


def pt_calibration(panel, horizon, max_ret=MAX_RET):
    """Do larger implied price-target upsides precede larger realized moves?"""
    band_r = [[] for _ in _PT_BANDS]
    implied_all, realized_all = [], []
    for d0, d1 in _windows(panel, horizon):
        fr = forward_returns(panel, d0, d1, max_ret)
        pt, p0 = panel.ptarget.get(d0, {}), panel.price.get(d0, {})
        for t, r in fr.items():
            if t in pt and t in p0 and p0[t] > 0:
                implied = pt[t] / p0[t] - 1
                if abs(implied) > max_ret:          # ignore absurd targets
                    continue
                implied_all.append(implied)
                realized_all.append(r)
                for bi, (lo, hi) in enumerate(_PT_BANDS):
                    if lo <= implied < hi:
                        band_r[bi].append(r)
                        break
    return {
        "horizon": horizon,
        "labels": _PT_LABELS,
        "band_means": [_mean(b) for b in band_r],
        "band_counts": [len(b) for b in band_r],
        "n": len(implied_all),
        "corr": spearman(implied_all, realized_all) if len(implied_all) >= 3 else None,
    }
