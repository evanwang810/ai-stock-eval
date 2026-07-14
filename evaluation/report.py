"""
report.py - run every metric over the panel and render a readable scorecard.

build() returns a plain dict (JSON-dumpable, good for feeding the site later);
render_text() turns it into the terminal report. All the statistical hedging
lives here so the metrics stay honest and unopinionated.
"""

from __future__ import annotations

from . import metrics
from .panel import SIGNALS, load_panel


def build(panel, horizons, signals=SIGNALS, topn=20, q=5):
    """Compute the full metric set. Returns a JSON-serializable dict."""
    horizons = [h for h in horizons if h < len(panel.dates)]
    return {
        "dates":        panel.dates,
        "dropped":      panel.dropped,
        "universe":     len(panel.universe()),
        "horizons":     horizons,
        "primary":      signals[0],
        "ic": {
            s: {h: metrics.rank_ic(panel, s, h) for h in horizons} for s in signals
        },
        "topn": {
            s: {h: metrics.topn_long_only(panel, s, h, n=topn) for h in horizons}
            for s in signals
        },
        "quantiles": {
            h: metrics.quantile_spread(panel, signals[0], h, q=q) for h in horizons
        },
        "pt": {h: metrics.pt_calibration(panel, h) for h in horizons},
        "params": {"topn": topn, "q": q},
    }


# ── rendering ───────────────────────────────────────────────────────────────

def _pct(v, width=7):
    return f"{v * 100:+{width - 1}.1f}%" if isinstance(v, (int, float)) else " " * (width - 3) + "n/a"


def _ic(v):
    return f"{v:+.3f}" if isinstance(v, (int, float)) else "  n/a"


def _line(char="-", n=64):
    return char * n


def render_text(r):
    out = []
    W = 64
    out.append(_line("="))
    out.append("  FORWARD-RETURN EVAL  -  does the score predict what goes up?")
    out.append(_line("="))
    out.append(f"  scans used : {len(r['dates'])}  ({r['dates'][0]} -> {r['dates'][-1]})")
    if r["dropped"]:
        out.append(f"  dropped    : {', '.join(r['dropped'])}  (stale / weekend re-snapshots)")
    out.append(f"  universe   : {r['universe']} distinct tickers")
    out.append(f"  horizon    : N scans forward (not calendar days)")

    n_scans = len(r["dates"])
    if n_scans < 12:
        out.append("")
        out.append("  ** SMALL SAMPLE. windows overlap and barely span two weeks, so")
        out.append("     every number below is noise-dominated. this is the plumbing;")
        out.append("     the readings get trustworthy as scans accumulate (aim 30+). **")

    # signal horse-race: mean IC per signal x horizon
    out.append("")
    out.append(_line())
    out.append("  RANK IC  (Spearman signal vs forward return; 0=no skill, .05+=edge)")
    out.append(_line())
    hs = r["horizons"]
    head = "  signal      " + "".join(f"  h={h} (n)   " for h in hs)
    out.append(head)
    for s in r["ic"]:
        row = f"  {s:<11}"
        for h in hs:
            d = r["ic"][s][h]
            row += f"  {_ic(d['mean_ic'])} ({d['n_windows']:>2})"
        out.append(row)
    out.append("  (n = number of overlapping windows averaged)")

    # top-N long-only excess, primary vs others
    out.append("")
    out.append(_line())
    out.append(f"  TOP-{r['params']['topn']} EXCESS  (mean pick return minus universe, per horizon)")
    out.append(_line())
    out.append("  signal      " + "".join(f"  h={h}     " for h in hs))
    for s in r["topn"]:
        row = f"  {s:<11}"
        for h in hs:
            row += f"  {_pct(r['topn'][s][h]['avg_excess'])}"
        out.append(row)

    # quantile buckets for the primary signal
    prim = r["primary"]
    out.append("")
    out.append(_line())
    out.append(f"  QUANTILE BUCKETS  ({prim}, low->high signal; forward return)")
    out.append(_line())
    for h in hs:
        qd = r["quantiles"][h]
        cells = "  ".join(_pct(m, 8) for m in qd["bucket_means"])
        out.append(f"  h={h}:  {cells}")
        sp = qd["spread"]
        out.append(f"        top-bottom spread {_pct(sp)}   long-only excess {_pct(qd['long_only_excess'])}")

    # price-target calibration
    out.append("")
    out.append(_line())
    out.append("  PRICE-TARGET CALIBRATION  (implied upside band -> realized return)")
    out.append(_line())
    for h in hs:
        cal = r["pt"][h]
        out.append(f"  h={h}  (corr {_ic(cal['corr'])}, n={cal['n']})")
        for lbl, m, c in zip(cal["labels"], cal["band_means"], cal["band_counts"]):
            out.append(f"        {lbl:<6} {_pct(m)}   (n={c})")

    out.append(_line("="))
    out.append(_verdict(r))
    out.append(_line("="))
    return "\n".join(out)


def _verdict(r):
    """One-line read of the primary signal's shortest-horizon IC."""
    prim = r["primary"]
    hs = r["horizons"]
    if not hs:
        return "  verdict: not enough distinct scans to form a single window yet."
    d = r["ic"][prim][hs[0]]
    ic = d["mean_ic"]
    if ic is None:
        return "  verdict: no valid windows for the primary signal yet."
    tag = ("looks predictive" if ic > 0.05 else
           "looks backwards (negative)" if ic < -0.05 else
           "no detectable skill")
    return (f"  verdict: {prim} IC {_ic(ic)} over {d['n_windows']} window(s) -> {tag}. "
            f"too few scans to trust; recheck as data grows.")


def run(raw_dir=None, horizons=(1, 2, 3), **kw):
    panel = load_panel(raw_dir) if raw_dir else load_panel()
    return panel, build(panel, list(horizons), **kw)
