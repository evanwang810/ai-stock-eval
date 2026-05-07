"""
log.py - saves jsonl records of every run

logs/searches.jsonl
one json object per line
"""

import json, datetime
from pathlib import Path

_LOG_DIR  = Path(__file__).parent / "logs"
_LOG_FILE = _LOG_DIR / "searches.jsonl"


def save_log(entry: dict):
    """Append one search record to the log file."""
    _LOG_DIR.mkdir(exist_ok=True)
    entry = dict(entry)
    entry["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def load_logs(n: int = 50) -> list:
    """Return up to n most recent log entries (newest last)."""
    if not _LOG_FILE.exists():
        return []
    entries = []
    with open(_LOG_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries[-n:]
