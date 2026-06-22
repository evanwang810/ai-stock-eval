"""
update_universe.py - refresh the ticker universe to the top-N US stocks by
market cap (NASDAQ + NYSE), keep it sticky, and flag changes.

Ranks every NASDAQ + NYSE listing by market cap via the Nasdaq screener and
keeps the top TOP_N, normalized to yfinance tickers (BRK/B -> BRK-B). This
catches big names the S&P 500 misses - foreign ADRs like TSM, and newer large
caps that haven't been added to the index yet.

Sticky universe: once a stock has been tracked, it KEEPS getting scanned even
after it slips below rank TOP_N - so positions don't vanish mid-stream just
because a name drifted from #480 to #520. A grandfathered stock is only retired
when it stops trading entirely (delisted / acquired - i.e. it disappears from
the screener), or when the universe would exceed MAX_UNIVERSE, in which case the
smallest grandfathered names are trimmed first. The active top TOP_N is never
trimmed.

Writes watcher/universe.json and diffs against the previous run so additions
and removals are logged - run daily before the scan so the universe stays
current. The watcher prefers universe.json over its built-in top-100 list; if
this has never run, it falls back to the top-100.

Note: the Nasdaq screener is an unofficial endpoint. On failure the existing
universe.json is kept untouched, so a bad fetch never empties the scan.

Standalone:  python scripts/update_universe.py
"""

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT          = Path(__file__).resolve().parent.parent
UNIVERSE_FILE = ROOT / "watcher" / "universe.json"
EXCHANGES     = ("NASDAQ", "NYSE")
TOP_N         = 500
MAX_UNIVERSE  = 750   # hard ceiling so grandfathered names can't balloon forever
MIN_EXPECTED  = 400   # sanity floor before we overwrite the existing file
_HEADERS      = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
}


def _parse_cap(raw):
    """'4,965,598,000,000' -> 4965598000000.0, '' -> 0.0"""
    s = (raw or "").replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _norm(symbol):
    """Nasdaq symbol -> yfinance ticker; '' if it's not analyzable."""
    s = (symbol or "").strip().upper()
    if not s or any(c in s for c in "^~ "):
        return ""
    return s.replace("/", "-").replace(".", "-")  # BRK/B -> BRK-B


# keep operating-company common stock + common ADRs; drop preferreds, baby
# bonds, funds, warrants, units. "depositary shares" alone is NOT excluded -
# common ADRs (e.g. TSM) carry that wording; only "preferred" / "%" do.
_NOT_COMMON = ("preferred", "%", " etf", "etf ", " etn", " fund",
               " trust ", "warrant", " notes", " units", "depositary shs pfd")


def _is_common(name):
    n = (name or "").lower()
    return not any(bad in n for bad in _NOT_COMMON)


def fetch_ranked():
    """Return [(ticker, marketcap), ...] across all exchanges, cap-desc."""
    rows = []
    for ex in EXCHANGES:
        url = (f"https://api.nasdaq.com/api/screener/stocks"
               f"?tableonly=true&limit=600&offset=0&exchange={ex}")
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        d = data.get("data") or {}
        ex_rows = (d.get("table") or {}).get("rows") or d.get("rows") or []
        for row in ex_rows:
            t = _norm(row.get("symbol"))
            cap = _parse_cap(row.get("marketCap"))
            if t and cap > 0 and _is_common(row.get("name")):
                rows.append((t, cap))
    # dedupe keeping the largest cap seen per ticker, then sort desc
    best = {}
    for t, cap in rows:
        if cap > best.get(t, 0):
            best[t] = cap
    return sorted(best.items(), key=lambda kv: kv[1], reverse=True)


def load_previous():
    if UNIVERSE_FILE.exists():
        try:
            return set(json.loads(UNIVERSE_FILE.read_text(encoding="utf-8-sig")).get("tickers", []))
        except Exception:
            pass
    return set()


def main():
    try:
        ranked = fetch_ranked()
    except Exception as e:
        print(f"[update_universe] fetch failed: {e}", file=sys.stderr)
        print("[update_universe] keeping existing universe.json (if any)")
        return 0

    top = [t for t, _ in ranked[:TOP_N]]
    if len(top) < MIN_EXPECTED:
        print(f"[update_universe] only {len(top)} tickers (< {MIN_EXPECTED}); "
              "looks wrong, keeping existing universe.json", file=sys.stderr)
        return 0

    cap_by = dict(ranked)          # every still-trading common listing -> cap
    prev   = load_previous()

    # sticky: keep previously-tracked names that still trade but have slipped
    # below TOP_N, ordered by cap so the ceiling trim drops the smallest first.
    # names that vanished from the screener entirely (delisted / acquired) fall
    # out naturally because they aren't in cap_by.
    grandfathered = sorted(
        (prev - set(top)) & set(cap_by),
        key=lambda t: cap_by[t], reverse=True,
    )

    tickers = top + grandfathered
    trimmed = []
    if len(tickers) > MAX_UNIVERSE:
        trimmed = tickers[MAX_UNIVERSE:]      # smallest grandfathered names
        tickers = tickers[:MAX_UNIVERSE]

    added = sorted(set(tickers) - prev)
    gone  = sorted(prev - set(tickers))       # delisted, or trimmed at the ceiling

    UNIVERSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    UNIVERSE_FILE.write_text(json.dumps({
        "tickers":       tickers,
        "count":         len(tickers),
        "top_n":         len([t for t in tickers if t in set(top)]),
        "grandfathered": len(tickers) - len([t for t in tickers if t in set(top)]),
        "updated":       datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source":        f"top {TOP_N} by market cap, {'+'.join(EXCHANGES)} "
                         f"(Nasdaq screener), sticky up to {MAX_UNIVERSE}",
    }, indent=2) + "\n", encoding="utf-8")

    print(f"[update_universe] wrote {len(tickers)} tickers "
          f"({len(top)} in top {TOP_N} + {len(grandfathered)} grandfathered) "
          f"of {len(ranked)} ranked")
    if added:
        print(f"[update_universe] ADDED:    {', '.join(added)}")
    if gone:
        why = "delisted/acquired" if not trimmed else "delisted/acquired or trimmed at ceiling"
        print(f"[update_universe] REMOVED ({why}): {', '.join(gone)}")
    if not prev:
        print("[update_universe] (first run - no previous list to diff)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
