"""
update_manifest.py - regenerate data/index.json + the per-day summaries.

Two outputs, both consumed by the static viewer (data/index.html):
  - data/index.json            list of available scan dates (raw file paths)
  - data/summary/<date>.json   a tiny per-day file with just the fields the
                               table and over-time charts need (ticker, score,
                               outlook, price, trend %s). The viewer loads these
                               up front - they're small - and lazy-loads the
                               heavy detail (LLM output, parsed reasons, full
                               facts) from data/raw only when a stock is opened.

Regenerates every summary from data/raw each run, so it backfills existing data
and stays correct if the raw records change.

Standalone:
  python scripts/update_manifest.py
"""

import datetime as dt
import json
from pathlib import Path

ROOT        = Path(__file__).resolve().parent.parent
DATA_DIR    = ROOT / "data"
RAW_DIR     = DATA_DIR / "raw"
SUMMARY_DIR = DATA_DIR / "summary"
OUT         = DATA_DIR / "index.json"

# the only fact fields the table + score/price/gain charts read; everything
# else stays in data/raw and is fetched on demand
_FACT_KEYS = (
    "companyName", "sector", "industry", "price", "dayChangePct",
    "weekTrendPct", "oneMonthTrendPct", "threeMonthTrendPct",
    "sixMonthTrendPct", "yearTrendPct",
)


def _summarize(rec):
    """Light projection of a full record, or None for failures/unscored rows."""
    sc = rec.get("scores") or {}
    if sc.get("blended") is None:
        return None
    f = rec.get("facts") or {}
    return {
        "ticker": rec.get("ticker"),
        "ts":     rec.get("ts"),
        "scores": {"blended": sc.get("blended"), "outlook": sc.get("outlook")},
        "facts":  {k: f[k] for k in _FACT_KEYS if f.get(k) is not None},
    }


def main():
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    raw_files = sorted(RAW_DIR.glob("*.jsonl"))

    for rf in raw_files:
        rows = []
        for line in rf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            s = _summarize(rec)
            if s:
                rows.append(s)
        (SUMMARY_DIR / f"{rf.stem}.json").write_text(
            json.dumps(rows, separators=(",", ":")) + "\n", encoding="utf-8")

    files = sorted(p.relative_to(DATA_DIR).as_posix() for p in raw_files)
    manifest = {
        "files":   files,
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "count":   len(files),
    }
    OUT.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote manifest ({len(files)} days) + {len(files)} summary file(s)")


if __name__ == "__main__":
    main()
