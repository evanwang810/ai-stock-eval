"""
history.py - historical daily prices via Alpha Vantage (a source besides yfinance)

Daily OHLC going back ~20 years. Used for backtests, not the live daily scan.
Free tier is ~25 requests/day, so cache what you pull.

  set ALPHAVANTAGE_API_KEY in your env / .env  (free: alphavantage.co/support/#api-key)

standalone:
  py history.py AAPL
  py history.py AAPL 2020-01-01 2021-01-01     # date-filtered

Note: this gives historical *prices* only. Point-in-time fundamentals (what the
P/E was 3 yrs ago) need a paid data vendor - free sources don't have them.
So: price/technical backtests only.
"""

import os
import sys
import json
import time
import urllib.request
import urllib.parse

# auto-load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

_BASE = "https://www.alphavantage.co/query"


def _get(params):
    url = f"{_BASE}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())


def fetch_daily_history(symbol, api_key=None, full=True):
    """
    Daily OHLCV for `symbol`, oldest-first.
    Returns list of dicts: {date, open, high, low, close, volume}.
    `full=True` grabs the whole 20yr history, False just the last ~100 days.

    Raises RuntimeError if AV hands back one of its many flavors of "no".
    """
    api_key = api_key or os.environ.get("ALPHAVANTAGE_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "no ALPHAVANTAGE_API_KEY set - grab a free one at "
            "alphavantage.co/support/#api-key"
        )

    data = _get({
        "function":   "TIME_SERIES_DAILY",
        "symbol":     symbol.upper(),
        "outputsize": "full" if full else "compact",
        "apikey":     api_key,
    })

    # AV reports errors in the response body, under varying keys
    if "Time Series (Daily)" not in data:
        msg = data.get("Note") or data.get("Information") or data.get("Error Message") or str(data)[:200]
        raise RuntimeError(f"Alpha Vantage said no for {symbol}: {msg}")

    series = data["Time Series (Daily)"]
    rows = []
    for date in sorted(series):  # sorted() on ISO dates == chronological, oldest first
        d = series[date]
        rows.append({
            "date":   date,
            "open":   float(d["1. open"]),
            "high":   float(d["2. high"]),
            "low":    float(d["3. low"]),
            "close":  float(d["4. close"]),
            "volume": int(d["5. volume"]),
        })
    return rows


def filter_range(rows, start=None, end=None):
    """keep rows with start <= date <= end (ISO yyyy-mm-dd strings, either optional)"""
    out = rows
    if start:
        out = [r for r in out if r["date"] >= start]
    if end:
        out = [r for r in out if r["date"] <= end]
    return out


if __name__ == "__main__":
    sym   = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    start = sys.argv[2] if len(sys.argv) > 2 else None
    end   = sys.argv[3] if len(sys.argv) > 3 else None

    print(f"fetching {sym} history from Alpha Vantage ...")
    try:
        rows = fetch_daily_history(sym)
    except RuntimeError as e:
        print(f"failed: {e}")
        sys.exit(1)

    rows = filter_range(rows, start, end)
    if not rows:
        print("no rows in that date range")
        sys.exit(0)

    first, last = rows[0], rows[-1]
    print(f"{len(rows)} trading days  {first['date']} -> {last['date']}")
    print(f"first close ${first['close']:.2f}   last close ${last['close']:.2f}")
    chg = (last["close"] / first["close"] - 1) * 100 if first["close"] else 0
    print(f"period return: {chg:+.1f}%")
