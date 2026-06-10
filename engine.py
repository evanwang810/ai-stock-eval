"""
engine.py - analysis stuff with no ui
builds prompts, calls groq, parses responses, blends scores.
no ansi, no terminal stuff.

llm calls go through llm.py (the swappable provider layer).

exports:
  build_prompt(facts, det, headlines=None) -> str
  call_groq(client_or_pool, prompt, spinner=None) -> (str, usage | None)
  parse_response(text) -> dict
  blend_scores(ai_ratings, det_result) -> dict
  det_display(det) -> int

  MODEL, PROVIDER, PROVIDERS, KeyPool, make_client  (re-exported from llm.py)
  COMPONENT_ORDER, PROMPT_SYSTEM
"""

import re

from score import DEFAULT_WEIGHTS

# llm.py is the swappable provider layer - re-exported here so existing
# imports (main.py, watcher, tests) keep working unchanged
from llm import PROVIDERS, PROVIDER, MODEL, KeyPool, make_client, call_llm, load_keys

COMPONENT_ORDER = ["momentum", "valuation", "growth", "profitability", "risk", "technicals"]

PROMPT_SYSTEM = (
    "You are a senior equity research analyst at a top-tier investment bank. "
    "Produce only the structured analysis in the exact format shown - "
    "no preamble, no commentary outside the format."
)


# prompt builder

def build_prompt(facts, det, headlines=None):
    """builds the prompt string"""
    bd = det["breakdown"]
    f  = facts
    w  = DEFAULT_WEIGHTS

    def _det_norm(k):
        raw = bd.get(k, 0)
        mx  = w.get(k, 0.1) * 1000
        return int(round(max(-100, min(100, raw / mx * 100)))) if mx else 0

    def pct(k):
        v = f.get(k)
        return f"{v:+.1f}%" if isinstance(v, (int, float)) else "N/A"

    def val(k, fmt=".1f", prefix="", suffix=""):
        v = f.get(k)
        return f"{prefix}{v:{fmt}}{suffix}" if isinstance(v, (int, float)) else "N/A"

    stats = "\n".join(filter(None, [
        f"Price:            {val('price', '.2f', '$')}",
        f"Market cap:       {val('marketCapUSD', ',.0f', '$')}",
        "",
        "-- Momentum --",
        f"Day change:       {pct('dayChangePct')}",
        f"1 week:           {pct('weekTrendPct')}",
        f"1 month:          {pct('oneMonthTrendPct')}",
        f"3 months:         {pct('threeMonthTrendPct')}",
        f"6 months:         {pct('sixMonthTrendPct')}",
        f"1 year:           {pct('yearTrendPct')}",
        f"vs 52w high:      {pct('distFrom52wHigh')}",
        "",
        "-- Technicals --",
        f"RSI (14d):        {val('rsi14', '.0f')}",
        f"vs 50d SMA:       {pct('pctVsSma50')}",
        f"vs 200d SMA:      {pct('pctVsSma200')}",
        f"50d > 200d SMA:   {'yes (golden cross)' if f.get('goldenCross') is True else 'no (death cross)' if f.get('goldenCross') is False else 'N/A'}",
        f"Up-day ratio 3mo: {val('upDayRatio', '.2f')}",
        "",
        "-- Valuation --",
        f"P/E (TTM):        {val('peTTM', '.1f', suffix='x')}",
        f"P/B:              {val('pb', '.1f', suffix='x')}",
        f"EPS (TTM):        {val('epsTTM', '.2f', '$')}",
        f"Debt / Equity:    {val('debtToEquity', '.2f', suffix='x')}",
        "",
        "-- Growth --",
        f"Revenue growth:   {pct('revenueGrowthPct')}",
        f"EPS growth (TTM): {pct('epsGrowthTTMYoy')}",
        "",
        "-- Profitability --",
        f"Net margin:       {pct('netMarginPct')}",
        f"Gross margin:     {pct('grossMarginPct')}",
        f"ROE:              {pct('roeTTM')}",
        f"ROIC / ROA:       {pct('roic')}",
        "",
        "-- Risk --",
        f"Beta:             {val('beta', '.2f')}",
        f"Volatility (ann): {pct('volatilityAnnualPct')}",
        f"Dividend yield:   {pct('dividendYieldPct')}",
        f"Current ratio:    {val('currentRatio', '.2f')}",
    ]))

    ref_line = (
        "  ".join(f"{k}={_det_norm(k):+d}" for k in COMPONENT_ORDER)
        + "  (algorithmic signal, -100..+100, reference only)"
    )

    news_section = ""
    if headlines:
        news_section = (
            "\nRecent news headlines (use these to gauge investor sentiment and "
            "near-term catalysts - weigh tone, not just facts):\n"
            + "\n".join(f"* {h}" for h in headlines)
            + "\n"
        )

    company   = f.get("companyName", f.get("ticker", ""))
    ticker    = f.get("ticker", "")
    sector    = f.get("sector", "N/A")
    industry  = f.get("industry", "N/A")
    price_now = val("price", ".2f", "$")

    return f"""\
OUTPUT FORMAT - follow this EXACTLY. Begin your response with Line 1. No preamble.

Line 1  Six integers, -100 to +100, comma-separated, NO spaces, NO labels.
        Order: momentum,valuation,growth,profitability,risk,technicals
Line 2  PRICE TARGET: $NNN   (dollar sign then a number, nothing else on this line)
Line 3  Investment thesis sentence 1 (no label, plain text)
Line 4  Investment thesis sentence 2 (no label, plain text, continue the thesis)
Line 5  BUY:
Line 6  First buy catalyst (plain text, no bullet, no dash, no number prefix)
Line 7  Second buy catalyst
Line 8  Third buy catalyst
Line 9  Fourth buy catalyst
Line 10 SELL:
Line 11 First risk factor (plain text, no bullet, no dash, no number prefix)
Line 12 Second risk factor
Line 13 Third risk factor
Line 14 Fourth risk factor

EXAMPLE - your output must look exactly like this (different numbers/text for each stock):
45,-20,60,70,-30,10
PRICE TARGET: $213
Apple's services flywheel compounds as iCloud, Pay, and App Store deepen monetization across 2B+ devices.
Near-term margin expansion from software mix shift outweighs hardware replacement-cycle softness.
BUY:
Services growing 15%+ annually with near-zero incremental cost, expanding operating margins each quarter
Annual buyback retires 3-4% of shares outstanding, mechanically lifting EPS without organic growth
Ecosystem switching costs and brand pricing power protect installed-base economics from competitive disruption
Healthcare, payments, and advertising verticals represent unpriced optionality in current consensus models
SELL:
China revenue at 19% of sales represents an unhedgeable geopolitical concentration risk
At 29x forward earnings there is no margin of safety if the iPhone upgrade cycle misses two quarters
EU and US antitrust actions threaten the App Store economics that underpin 40%+ services-segment margins
Capex rising sharply for on-device AI chips and data-center infrastructure, pressuring near-term free cash flow

===== STOCK TO ANALYZE =====
Company:  {company}
Ticker:   {ticker}
Sector:   {sector}
Industry: {industry}

Business description:
{f.get('description', 'No description available.')}{news_section}
Financials:
{stats}

Algorithmic signal: {ref_line}

Analyze {company} ({ticker}) at {price_now}. Write Line 1 now:"""


# llm call - thin wrapper around the provider layer (legacy name kept
# so main.py / watcher / anything else importing call_groq still works)

def call_groq(client_or_pool, prompt, spinner=None):
    """Call the configured LLM provider with the analyst system prompt."""
    return call_llm(client_or_pool, PROMPT_SYSTEM, prompt, spinner=spinner)


# response parser

def parse_response(text):
    """
    3-pass parser that doesn't care if the llm messes up formatting

    pass 1: looks for 6 comma-separated ints on a line
    pass 2: regex scan for any 6 ints anywhere
    pass 3: looks for "momentum: 45" style labels

    returns dict:
      ratings: dict or empty on fail
      price_target: float or None
      explanation: str
      buy_reasons: list (max 4)
      sell_reasons: list (max 4)
    """
    # Strip reasoning/think blocks emitted by some models
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"</?think>", "", text).strip()

    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # pass 1: look for 6 ints on a line
    ratings, ratings_idx = {}, None
    for i, line in enumerate(lines):
        clean = re.sub(r"[`*_\[\]#]", "", line)
        parts = [p.strip() for p in clean.split(",")]
        if len(parts) == 6:
            try:
                cleaned = [re.sub(r"[^\d.\-+]", "", p) for p in parts]
                if all(c for c in cleaned):
                    nums = [int(round(float(c))) for c in cleaned]
                    if all(-100 <= x <= 100 for x in nums):
                        ratings     = dict(zip(COMPONENT_ORDER, nums))
                        ratings_idx = i
                        break
            except (ValueError, OverflowError):
                pass

    # pass 2: regex scan for any 6 ints
    if ratings_idx is None:
        m = re.search(
            r"(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)",
            text,
        )
        if m:
            nums = [int(x) for x in m.groups()]
            if all(-100 <= x <= 100 for x in nums):
                ratings     = dict(zip(COMPONENT_ORDER, nums))
                ratings_idx = next((i for i, l in enumerate(lines) if m.group(0) in l), 0)

    # pass 3: look for labeled values like "momentum: 45"
    if ratings_idx is None:
        fallback = {}
        for k in COMPONENT_ORDER:
            m = re.search(rf"{k}\s*[=:]\s*([+-]?\d+)", text, re.I)
            if m:
                v = int(m.group(1))
                if -100 <= v <= 100:
                    fallback[k] = v
        if len(fallback) == 6:
            ratings     = fallback
            ratings_idx = 0

    if ratings_idx is None:
        return {
            "ratings": {}, "price_target": None, "explanation": text,
            "buy_reasons": [], "sell_reasons": [],
        }

    remaining = lines[ratings_idx + 1:]

    # ── Price target ──────────────────────────────────────────────────────────
    price_target = None
    pt_idx       = None
    for i, line in enumerate(remaining):
        m = re.search(r"PRICE\s*TARGET\s*[:\-]\s*\$?([\d,]+(?:\.\d+)?)", line, re.I)
        if m:
            price_target = float(m.group(1).replace(",", ""))
            pt_idx       = i
            break
    if pt_idx is not None:
        remaining = remaining[:pt_idx] + remaining[pt_idx + 1:]

    # find buy/sell sections
    buy_idx = sell_idx = None
    for i, line in enumerate(remaining):
        ul = re.sub(r"[^A-Z]", "", line.upper())
        if ul in ("BUY", "BUYCATALYSTS", "BUYREASONS", "BUYSIGNALS"):
            buy_idx = i
        elif ul in ("SELL", "SELLRISKS", "SELLREASONS", "RISKS", "RISKFACTORS"):
            sell_idx = i

    exp_end     = buy_idx if buy_idx is not None else len(remaining)
    explanation = " ".join(remaining[:exp_end]).strip()

    buy_reasons = sell_reasons = []
    if buy_idx is not None:
        sell_start  = sell_idx if sell_idx is not None else len(remaining)
        buy_reasons = [
            l.lstrip("-•*+▸›✓#>1234567890.) ")
            for l in remaining[buy_idx + 1 : sell_start] if l.strip()
        ]
    if sell_idx is not None:
        sell_reasons = [
            l.lstrip("-•*+▸›✗#>1234567890.) ")
            for l in remaining[sell_idx + 1 :] if l.strip()
        ]

    return {
        "ratings":      ratings,
        "price_target": price_target,
        "explanation":  explanation,
        "buy_reasons":  buy_reasons[:4],
        "sell_reasons": sell_reasons[:4],
    }


# score blending

def blend_scores(ai_ratings, det_result, weights=None):
    """
    blends ai + deterministic scores

    returns dict:
      blended_components: dict of ±100 per component
      blended: int ±1000 (60% ai + 40% det)
      ai_total: int ±1000
      det_total: int ±1000
      det_norm: dict ±100 each
      outlook: "BULLISH" | "NEUTRAL" | "BEARISH"
    """
    w = weights or DEFAULT_WEIGHTS

    # normalize det breakdown to ±100
    det_norm = {}
    for k in COMPONENT_ORDER:
        raw = det_result["breakdown"].get(k, 0)
        mx  = w.get(k, 0.1) * 1000
        det_norm[k] = int(round(max(-100, min(100, raw / mx * 100)))) if mx else 0

    # blend per-component: 60% ai + 40% det
    blended_components = {
        k: int(round(max(-100, min(100, 0.6 * ai_ratings.get(k, 0) + 0.4 * det_norm.get(k, 0)))))
        for k in COMPONENT_ORDER
    }

    # weighted totals to ±1000 (multiply before rounding for full precision)
    def _weighted_total(comp_dict):
        return int(round(max(-1000, min(1000,
            sum(comp_dict.get(k, 0) * w.get(k, 0) for k in COMPONENT_ORDER) * 10
        ))))

    total     = _weighted_total(blended_components)
    ai_total  = _weighted_total(ai_ratings)
    det_total = _weighted_total(det_norm)

    outlook = "BULLISH" if total > 150 else "BEARISH" if total < -150 else "NEUTRAL"

    return {
        "ai_total":           ai_total,
        "det_total":          det_total,
        "blended":            total,
        "blended_components": blended_components,
        "det_norm":           det_norm,
        "outlook":            outlook,
    }


def det_display(det):
    """deterministic score normalized to ±1000"""
    dn = {
        k: max(-100, min(100, det["breakdown"].get(k, 0) / (DEFAULT_WEIGHTS.get(k, 0.1) * 1000) * 100))
        for k in COMPONENT_ORDER
        if DEFAULT_WEIGHTS.get(k, 0.1) * 1000
    }
    total_100 = sum(dn.get(k, 0) * DEFAULT_WEIGHTS.get(k, 0) for k in COMPONENT_ORDER)
    return int(round(max(-1000, min(1000, total_100 * 10))))
