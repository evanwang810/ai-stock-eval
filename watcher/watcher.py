"""
watcher.py - headless daily stock analyzer

Fetches and scores a list of tickers on a schedule.
Designed to run continuously on a server (VPS, Raspberry Pi, etc.).

Usage:
  python watcher.py                     # uses config.json, runs forever
  python watcher.py --once              # one cycle then exit
  python watcher.py --config my.json    # custom config file

Config: copy config.example.json -> config.json and edit it.
Keys: put them in ../keys.txt (one per line) - easiest and gitignored.

Environment variable overrides (useful for Docker / systemd / GitHub Actions):
  LLM_API_KEYS    comma- or newline-separated keys
  FINNHUB_KEY     optional Finnhub key
  TICKERS         comma-separated tickers  e.g. "AAPL,MSFT,NVDA"
  LLM_PROVIDER    cerebras (default) | groq | gemini  - see llm.py
  LLM_MODEL       override the model for any provider

Server deployment examples:

  # keep running with nohup (simple):
  nohup python watcher.py > watcher.log 2>&1 &

  # systemd service (recommended for Linux VPS):
  # copy watcher.service.example to /etc/systemd/system/stock-watcher.service
  # then: systemctl enable stock-watcher && systemctl start stock-watcher

Results are written to:
  watcher/results/YYYY-MM-DD_HHMMSS.json   one file per run
  watcher/results/watcher.log              running log (stdout also)
"""

import sys
import os
import json
import time
import signal
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path

# allow imports from parent directory (engine, fetch, score, log)
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine import KeyPool, build_prompt, call_groq, parse_response, blend_scores, load_keys
from fetch  import fetch_facts, fetch_finnhub_news
from score  import compute_score, DEFAULT_WEIGHTS
from log    import save_log


# ── Paths ─────────────────────────────────────────────────────────────────────

_WATCHER_DIR  = Path(__file__).parent
_RESULTS_DIR  = _WATCHER_DIR / "results"
_CONFIG_FILE  = _WATCHER_DIR / "config.json"
_EXAMPLE_FILE = _WATCHER_DIR / "config.example.json"   # fallback (has top-100 defaults)


# ── Config ────────────────────────────────────────────────────────────────────

_DEFAULTS = {
    "tickers":              [],
    "api_keys":             [],     # falls back to env via load_keys()
    "finnhub_key":          "",
    "interval_hours":       24,
    "run_on_start":         True,
    "retries_per_ticker":   2,      # extra attempts if the model misbehaves
    "skip_when_market_open": True,  # don't burn calls during US trading hours
}


def load_config(path=None):
    cfg = dict(_DEFAULTS)
    # explicit path > config.json > config.example.json (so the top-100 defaults
    # work on a fresh checkout / GitHub Actions where there's no config.json)
    if path:
        config_path = Path(path)
    elif _CONFIG_FILE.exists():
        config_path = _CONFIG_FILE
    else:
        config_path = _EXAMPLE_FILE
        log.info("No config.json - falling back to config.example.json defaults")

    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    else:
        log.warning(f"No config file at {config_path} - using env vars only")

    # keys: config value wins, otherwise env vars / .env (see llm.load_keys)
    if not cfg["api_keys"]:
        cfg["api_keys"] = load_keys()

    if os.environ.get("FINNHUB_KEY"):
        cfg["finnhub_key"] = os.environ["FINNHUB_KEY"]

    env_tickers = os.environ.get("TICKERS", "")
    if env_tickers:
        cfg["tickers"] = [t.strip().upper() for t in env_tickers.split(",") if t.strip()]

    return cfg


# ── Market hours ──────────────────────────────────────────────────────────────

def is_us_market_open(now=None):
    """
    Rough check: True during regular US trading hours (Mon-Fri 9:30-16:00 ET).
    Ignores market holidays - close enough to avoid scanning mid-session.
    """
    try:
        from zoneinfo import ZoneInfo
        now = now or datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return False  # if tz data is unavailable, don't block runs
    if now.weekday() >= 5:               # Sat/Sun
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


# ── Logging ───────────────────────────────────────────────────────────────────

def _setup_logging():
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    fh = logging.FileHandler(_RESULTS_DIR / "watcher.log", encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)


log = logging.getLogger(__name__)


# ── Per-ticker analysis ───────────────────────────────────────────────────────

def _analyze_ticker(symbol, key_pool, finnhub_key, retries=2):
    """
    Run a full headless analysis for one ticker.
    Retries the LLM call up to `retries` extra times if the model returns
    something unparseable (it sometimes ignores the format).
    Returns a result dict (success=True) or an error dict (success=False).
    """
    log.info(f"  [{symbol}] fetching market data ...")
    try:
        facts = fetch_facts(symbol, finnhub_key or None)
    except Exception as e:
        log.error(f"  [{symbol}] fetch failed: {e}")
        return {"ticker": symbol, "success": False, "error": f"fetch: {e}"}

    name   = facts.get("companyName", symbol)
    sector = facts.get("sector", "")
    log.info(f"  [{symbol}] {name}  {sector}")

    det = compute_score(facts, DEFAULT_WEIGHTS)

    # grab headlines if we have a finnhub key - feeds the sentiment read
    headlines = []
    if finnhub_key:
        try:
            headlines = fetch_finnhub_news(symbol, finnhub_key)
        except Exception:
            pass
        if headlines:
            log.info(f"  [{symbol}] {len(headlines)} headlines")

    prompt = build_prompt(facts, det, headlines or None)

    parsed = None
    last_err = "parse_failed"
    for attempt in range(retries + 1):
        tag = "" if attempt == 0 else f" (retry {attempt}/{retries})"
        log.info(f"  [{symbol}] calling LLM{tag} ...")
        try:
            raw, usage = call_groq(key_pool, prompt)
        except Exception as e:
            last_err = f"llm: {e}"
            log.error(f"  [{symbol}] LLM call failed: {e}")
            continue
        if usage:
            log.info(f"  [{symbol}] {getattr(usage, 'total_tokens', '?')} tokens used")
        candidate = parse_response(raw)
        if candidate["ratings"]:
            parsed = candidate
            break
        last_err = "parse_failed"
        log.warning(f"  [{symbol}] response unparseable{tag}")

    if parsed is None:
        log.error(f"  [{symbol}] giving up after {retries + 1} attempt(s): {last_err}")
        return {"ticker": symbol, "success": False, "error": last_err}

    scores  = blend_scores(parsed["ratings"], det)
    outlook = scores["outlook"]
    sign    = "+" if scores["blended"] >= 0 else ""
    log.info(f"  [{symbol}] score {sign}{scores['blended']}  {outlook}")

    # write to the shared log (shows up in main.py history)
    save_log({
        "ticker":             symbol,
        "company":            name,
        "success":            True,
        "blended":            scores["blended"],
        "ai_total":           scores["ai_total"],
        "det_total":          scores["det_total"],
        "outlook":            outlook,
        "sector":             sector,
        "price_target":       parsed.get("price_target"),
        "blended_components": scores["blended_components"],
    })

    return {
        "ticker":             symbol,
        "company":            name,
        "sector":             sector,
        "success":            True,
        "price":              facts.get("price"),
        "blended":            scores["blended"],
        "ai_total":           scores["ai_total"],
        "det_total":          scores["det_total"],
        "outlook":            outlook,
        "price_target":       parsed.get("price_target"),
        "blended_components": scores["blended_components"],
        "explanation":        parsed.get("explanation", ""),
        "buy_reasons":        parsed.get("buy_reasons", []),
        "sell_reasons":       parsed.get("sell_reasons", []),
    }


# ── One full run cycle ────────────────────────────────────────────────────────

def run_cycle(cfg):
    tickers = [t.upper() for t in cfg.get("tickers", []) if t]
    keys    = cfg.get("api_keys", [])
    finnhub = cfg.get("finnhub_key", "")
    retries = cfg.get("retries_per_ticker", 2)

    if not tickers:
        log.warning("No tickers configured - skipping cycle")
        return []
    if not keys:
        log.error("No API keys configured - cannot run cycle")
        return []

    if cfg.get("skip_when_market_open", True) and is_us_market_open():
        log.info("US market is open - skipping cycle (set skip_when_market_open=false to override)")
        return []

    pool = KeyPool(keys)
    log.info(f"Cycle start: {len(tickers)} ticker(s), {len(pool)} API key(s)")

    results = []
    for i, symbol in enumerate(tickers):
        result = _analyze_ticker(symbol, pool, finnhub, retries=retries)
        results.append(result)
        # small pause between tickers so we don't hammer rate limits
        if i < len(tickers) - 1:
            time.sleep(3)

    # save full results to a timestamped JSON file
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts       = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    out_path = _RESULTS_DIR / f"{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "run_at":           datetime.now(timezone.utc).isoformat(),
                "tickers_analyzed": len(results),
                "results":          results,
            },
            f,
            indent=2,
        )

    ok = sum(1 for r in results if r.get("success"))
    log.info(f"Cycle complete: {ok}/{len(results)} succeeded - {out_path.name}")
    return results


# ── Sleep helper ──────────────────────────────────────────────────────────────

_stop = False


def _handle_signal(sig, frame):
    global _stop
    log.info("Shutdown signal received - finishing current work ...")
    _stop = True


def _sleep(seconds):
    """Sleep in small increments so signals are handled promptly."""
    end = time.monotonic() + seconds
    while not _stop and time.monotonic() < end:
        time.sleep(min(5, max(0, end - time.monotonic())))


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    global _stop

    parser = argparse.ArgumentParser(description="Headless daily stock watcher")
    parser.add_argument("--config", default=None, help="Path to config.json")
    parser.add_argument("--once",   action="store_true", help="Run one cycle then exit")
    args = parser.parse_args()

    _setup_logging()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    cfg = load_config(args.config)

    if not cfg.get("api_keys"):
        log.error(
            "No API keys found. Put them in keys.txt (one per line), "
            "set api_keys in config.json, or set the LLM_API_KEYS env var."
        )
        sys.exit(1)

    if not cfg.get("tickers"):
        log.error("No tickers configured. Add tickers to config.json or set TICKERS env var.")
        sys.exit(1)

    interval     = cfg.get("interval_hours", 24)
    run_on_start = cfg.get("run_on_start", True)

    log.info(
        f"Watcher started | tickers: {len(cfg['tickers'])} | "
        f"keys: {len(cfg['api_keys'])} | interval: {interval}h | "
        f"provider: {os.environ.get('LLM_PROVIDER', 'cerebras')}"
    )

    if args.once:
        run_cycle(cfg)
        return

    if not run_on_start:
        log.info(f"Waiting {interval}h before first run ...")
        _sleep(interval * 3600)

    while not _stop:
        run_cycle(cfg)
        if _stop:
            break
        log.info(f"Next cycle in {interval}h")
        _sleep(interval * 3600)

    log.info("Watcher stopped.")


if __name__ == "__main__":
    main()
