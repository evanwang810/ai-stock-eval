"""
fetch.py - pulls facts for a ticker

primary: yfinance (free, no key)
optional: finnhub (better quotes)
news: finnhub (testing only)

standalone:
  py fetch.py AAPL
  py fetch.py TSLA YOUR_FINNHUB_KEY
"""

import sys
import json
import urllib.request
import urllib.error

try:
    import yfinance as yf
except ImportError:
    print("yfinance not installed.  Run: py -m pip install yfinance")
    sys.exit(1)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe(info, key, scale=1):
    """Get a numeric value from yfinance info dict, optionally scaled."""
    v = info.get(key)
    if v is None or not isinstance(v, (int, float)):
        return None
    return v * scale


def _pct_change(closes, n):
    """Percent change over the last n trading days."""
    if closes is None or len(closes) < n + 1:
        return None
    first = closes[-n - 1]
    last  = closes[-1]
    if not first or first == 0:
        return None
    return ((last - first) / abs(first)) * 100


def _compute_technicals(closes):
    """
    Derive technical indicators from a list of daily closes (oldest first).
    All computed locally from history we already pulled - no extra API calls,
    no TA library, just math i half-remember. closes is oldest-first, [-1] = today.
    """
    out = {}
    if not closes or len(closes) < 15:
        return out  # not enough bars to say anything useful, bail
    price = closes[-1]

    # RSI(14) - simple average of gains/losses over the last 14 sessions
    deltas = [closes[i] - closes[i - 1] for i in range(len(closes) - 14, len(closes))]
    gains  = sum(d for d in deltas if d > 0)
    losses = sum(-d for d in deltas if d < 0)
    if gains + losses > 0:
        out["rsi14"] = 100 * gains / (gains + losses)

    # Moving-average structure
    sma50 = sma200 = None
    if len(closes) >= 50:
        sma50 = sum(closes[-50:]) / 50
        if sma50:
            out["pctVsSma50"] = (price / sma50 - 1) * 100
    if len(closes) >= 200:
        sma200 = sum(closes[-200:]) / 200
        if sma200:
            out["pctVsSma200"] = (price / sma200 - 1) * 100
        if sma50 and sma200:
            out["goldenCross"] = sma50 > sma200

    # Daily returns → volatility + trend consistency
    rets = [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes)) if closes[i - 1]
    ]
    if len(rets) >= 20:
        window = rets[-126:]  # ~6 months
        mean   = sum(window) / len(window)
        var    = sum((r - mean) ** 2 for r in window) / len(window)
        out["volatilityAnnualPct"] = (var ** 0.5) * (252 ** 0.5) * 100
    if len(rets) >= 30:
        recent = rets[-63:]   # ~3 months
        out["upDayRatio"] = sum(1 for r in recent if r > 0) / len(recent)

    return out


def _http_get(url):
    """Simple HTTP GET → parsed JSON, or None on failure."""
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


# ── yfinance ──────────────────────────────────────────────────────────────────

def fetch_from_yfinance(symbol):
    """Fetch all available facts from yfinance. Returns a stripped facts dict."""
    t    = yf.Ticker(symbol.upper())
    info = t.info

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    prev  = info.get("previousClose") or info.get("regularMarketPreviousClose")
    hi52  = info.get("fiftyTwoWeekHigh")
    lo52  = info.get("fiftyTwoWeekLow")

    day_chg = info.get("regularMarketChangePercent")
    if day_chg is None and price and prev and prev != 0:
        day_chg = ((price - prev) / prev) * 100

    hist   = t.history(period="1y", interval="1d")
    closes = list(hist["Close"]) if not hist.empty else None

    dist_hi = ((price / hi52) - 1) * 100 if price and hi52 and hi52 != 0 else None
    dist_lo = ((price / lo52) - 1) * 100 if price and lo52 and lo52 != 0 else None

    # yfinance debtToEquity is already in % (150 = 1.5× ratio) - divide by 100
    de_raw = _safe(info, "debtToEquity")
    debt_to_equity = de_raw / 100 if de_raw is not None else None

    facts = {
        "ticker":      symbol.upper(),
        "companyName": info.get("longName") or info.get("shortName") or symbol,
        "description": (info.get("longBusinessSummary") or "")[:900],
        "sector":      info.get("sector")   or "",
        "industry":    info.get("industry") or "",
        "exchange":    info.get("exchange") or "",
        "country":     info.get("country")  or "",
        "price":       price,

        "dayChangePct":       day_chg,
        "weekTrendPct":       _pct_change(closes, 5),
        "oneMonthTrendPct":   _pct_change(closes, 21),
        "threeMonthTrendPct": _pct_change(closes, 63),
        "sixMonthTrendPct":   _pct_change(closes, 126),
        "yearTrendPct":       _pct_change(closes, 252),

        "peTTM":        _safe(info, "trailingPE"),
        "pb":           _safe(info, "priceToBook"),
        "epsTTM":       _safe(info, "trailingEps"),
        "debtToEquity": debt_to_equity,

        # yfinance returns growth as decimals → ×100
        "revenueGrowthPct": _safe(info, "revenueGrowth",  100),
        "epsGrowthTTMYoy":  _safe(info, "earningsGrowth", 100),

        # profitability also decimals → ×100
        "netMarginPct":   _safe(info, "profitMargins",  100),
        "roeTTM":         _safe(info, "returnOnEquity", 100),
        "roic":           _safe(info, "returnOnAssets", 100),  # ROA is best yfinance proxy for ROIC
        "grossMarginPct": _safe(info, "grossMargins",   100),

        "beta":             _safe(info, "beta"),
        # dividendYield is already in % in yfinance (0.38 → 0.38%) - no ×100
        "dividendYieldPct": _safe(info, "dividendYield"),
        "currentRatio":     _safe(info, "currentRatio"),

        "distFrom52wHigh": dist_hi,
        "distFrom52wLow":  dist_lo,
        "latestVolume":    info.get("volume"),
        "avgVolume10d":    info.get("averageVolume10days") or info.get("averageDailyVolume10Day"),

        "marketCapUSD": info.get("marketCap"),
    }

    facts.update(_compute_technicals(closes))

    return {k: v for k, v in facts.items() if v is not None and v != ""}


# ── Finnhub (optional fact supplement) ───────────────────────────────────────

def fetch_from_finnhub(symbol, api_key):
    """
    Fetch real-time quote + profile + metrics from Finnhub.
    Returns a partial facts dict; Finnhub data takes priority where available.
    """
    base = "https://finnhub.io/api/v1"
    sym  = symbol.upper()

    quote   = _http_get(f"{base}/quote?symbol={sym}&token={api_key}")
    profile = _http_get(f"{base}/stock/profile2?symbol={sym}&token={api_key}")
    metrics = _http_get(f"{base}/stock/metric?symbol={sym}&metric=all&token={api_key}")
    m = (metrics or {}).get("metric", {})

    price = quote.get("c")  if quote else None
    prev  = quote.get("pc") if quote else None
    hi52  = m.get("52WeekHigh")
    lo52  = m.get("52WeekLow")

    day_chg = quote.get("dp") if quote else None
    if day_chg is None and price and prev and prev != 0:
        day_chg = ((price - prev) / prev) * 100

    dist_hi = ((price / hi52) - 1) * 100 if price and hi52 and hi52 != 0 else None
    dist_lo = ((price / lo52) - 1) * 100 if price and lo52 and lo52 != 0 else None

    mkt_cap = None
    if profile and profile.get("marketCapitalization"):
        mkt_cap = profile["marketCapitalization"] * 1e6  # Finnhub gives millions

    def _m(key, scale=1):
        v = m.get(key)
        return v * scale if isinstance(v, (int, float)) else None

    facts = {
        "price":        price,
        "dayChangePct": day_chg,

        "companyName":  (profile or {}).get("name"),
        "description":  ((profile or {}).get("description") or "")[:900] or None,
        "sector":       (profile or {}).get("finnhubIndustry") or (profile or {}).get("gics") or None,
        "exchange":     (profile or {}).get("exchange"),
        "country":      (profile or {}).get("country"),
        "marketCapUSD": mkt_cap,

        "peTTM":        _m("peTTM") or _m("peNormalizedAnnual"),
        "pb":           _m("pbAnnual"),
        "epsTTM":       _m("epsTTM"),
        "debtToEquity": _m("totalDebt2EquityAnnual"),  # Finnhub already a ratio

        "revenueGrowthPct": _m("revenueGrowthTTMYoy"),
        "epsGrowthTTMYoy":  _m("epsGrowthTTMYoy"),
        "epsGrowthQtrYoy":  _m("epsGrowthQuarterlyYoy"),

        "netMarginPct":   _m("netMargin"),
        "roeTTM":         _m("roeTTM"),
        "roic":           _m("roicAnnual"),
        "grossMarginPct": _m("grossMarginTTM"),

        "beta":             _m("beta"),
        "dividendYieldPct": _m("dividendYieldIndicatedAnnual"),
        "currentRatio":     _m("currentRatioAnnual"),

        "distFrom52wHigh": dist_hi,
        "distFrom52wLow":  dist_lo,
        "avgVolume10d":    _m("10DayAverageTradingVolume"),
    }

    return {k: v for k, v in facts.items() if v is not None and v != ""}


# ── Finnhub news (testing mode only) ─────────────────────────────────────────

def fetch_finnhub_news(symbol, api_key, n=5):
    """
    Fetch recent company news headlines from Finnhub.
    Returns up to n headline strings.
    Called in IS_TESTING mode only - NOT a fact supplement.
    """
    import datetime
    today   = datetime.date.today().isoformat()
    from_dt = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    url = (f"https://finnhub.io/api/v1/company-news"
           f"?symbol={symbol.upper()}&from={from_dt}&to={today}&token={api_key}")
    data = _http_get(url)
    if not data or not isinstance(data, list):
        return []
    seen, headlines = set(), []
    for item in data:
        h = (item.get("headline") or "").strip()
        if h and h not in seen:
            seen.add(h)
            headlines.append(h)
        if len(headlines) >= n:
            break
    return headlines


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_facts(symbol, finnhub_key=None):
    """
    Fetch a complete facts dict for symbol.
    yfinance is always the base; Finnhub merges in if a key is provided.
    """
    facts = fetch_from_yfinance(symbol)

    if finnhub_key:
        fh = fetch_from_finnhub(symbol, finnhub_key)
        if fh:
            facts.update({k: v for k, v in fh.items() if v is not None})

    return facts


# ── Standalone ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    symbol      = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    finnhub_key = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"Fetching {symbol}{'  (+ Finnhub)' if finnhub_key else '  (yfinance only)'}...\n")
    facts = fetch_facts(symbol, finnhub_key)
    print(json.dumps(facts, indent=2, default=str))

    try:
        from score import compute_score, DEFAULT_WEIGHTS
        result = compute_score(facts, DEFAULT_WEIGHTS)
        print(f"\nScore: {result['total']:+d}")
        for k, v in result["breakdown"].items():
            bar  = "|" * abs(v // 5) if v != 0 else ""
            sign = "+" if v >= 0 else ""
            print(f"  {k:<14} {sign}{v:>5}  {bar}")
    except ImportError:
        pass
