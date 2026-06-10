"""
update_manifest.py - regenerate data/index.json after a scan

Lists data/raw/*.jsonl and writes the relative paths to data/index.json so the
static viewer (data/index.html) can discover all available scan files without
needing the GitHub API. Run after the watcher writes a new daily file.

Standalone:
  python scripts/update_manifest.py
"""

import datetime as dt
import glob
import json
from pathlib import Path

ROOT       = Path(__file__).resolve().parent.parent
DATA_DIR   = ROOT / "data"
RAW_DIR    = DATA_DIR / "raw"
OUT        = DATA_DIR / "index.json"


def main():
    files = sorted(
        p.relative_to(DATA_DIR).as_posix()  # 'raw/YYYY-MM-DD.jsonl'
        for p in RAW_DIR.glob("*.jsonl")
    )
    manifest = {
        "files":   files,
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "count":   len(files),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} with {len(files)} file(s)")


if __name__ == "__main__":
    main()
