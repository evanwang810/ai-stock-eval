"""
update_universe.py - refresh the S&P 500 ticker universe and flag changes.

Pulls the current S&P 500 constituents from the public datasets mirror,
normalizes tickers to yfinance format (BRK.B -> BRK-B), and writes
watcher/universe.json. Diffs against the previous file so additions/removals
are logged - run this daily before the scan so newly added companies aren't
missed.

The watcher prefers universe.json over its built-in top-100 list. If this
script has never run (no universe.json), the watcher falls back to the top-100.

Note: this is *current* index membership. A free list of companies "eligible
but not yet added" doesn't exist, so the daily diff is the practical substitute
- it picks up new names the day S&P adds them.

Standalone:  python scripts/update_universe.py
"""

import csv
import io
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT          = Path(__file__).resolve().parent.parent
UNIVERSE_FILE = ROOT / "watcher" / "universe.json"
SOURCE_URL    = ("https://raw.githubusercontent.com/datasets/"
                 "s-and-p-500-companies/main/data/constituents.csv")
MIN_EXPECTED  = 400   # sanity floor: a real S&P 500 list is ~500 names


def fetch_constituents(url=SOURCE_URL):
    """Download the constituents CSV and return sorted, yfinance-formatted tickers."""
    req = urllib.request.Request(url, headers={"User-Agent": "ai-stock-eval/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", "replace")
    reader = csv.DictReader(io.StringIO(raw))
    # find the symbol column regardless of header casing/naming
    sym_col = next((c for c in (reader.fieldnames or []) if "symbol" in c.lower()), None)
    if not sym_col:
        raise ValueError(f"no symbol column in headers: {reader.fieldnames}")
    out = []
    for row in reader:
        s = (row.get(sym_col) or "").strip().upper().replace(".", "-")  # BRK.B -> BRK-B
        if s:
            out.append(s)
    return sorted(set(out))


def load_previous():
    if UNIVERSE_FILE.exists():
        try:
            return set(json.loads(UNIVERSE_FILE.read_text(encoding="utf-8-sig")).get("tickers", []))
        except Exception:
            pass
    return set()


def main():
    try:
        tickers = fetch_constituents()
    except Exception as e:
        print(f"[update_universe] fetch failed: {e}", file=sys.stderr)
        print("[update_universe] keeping existing universe.json (if any)")
        return 0  # don't break the scan - the watcher uses the last good list

    if len(tickers) < MIN_EXPECTED:
        print(f"[update_universe] only {len(tickers)} tickers (< {MIN_EXPECTED}); "
              "looks wrong, keeping existing universe.json", file=sys.stderr)
        return 0

    prev  = load_previous()
    added = sorted(set(tickers) - prev)
    gone  = sorted(prev - set(tickers))

    UNIVERSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    UNIVERSE_FILE.write_text(json.dumps({
        "tickers": tickers,
        "count":   len(tickers),
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source":  SOURCE_URL,
    }, indent=2) + "\n", encoding="utf-8")

    print(f"[update_universe] wrote {len(tickers)} tickers")
    if added:
        print(f"[update_universe] ADDED:   {', '.join(added)}")
    if gone:
        print(f"[update_universe] REMOVED: {', '.join(gone)}")
    if not prev:
        print("[update_universe] (first run - no previous list to diff)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
