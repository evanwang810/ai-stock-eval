"""
engine.py  ──  analysis pipeline (no UI)

All pure-processing functions: prompt building, Groq API calls, response
parsing, score blending.  Nothing here prints or imports ANSI codes.

Public API:
    build_prompt(facts, det, headlines=None)  -> str
    call_groq(client, prompt, spinner=None)   -> (str, usage | None)
    parse_response(text)                      -> dict
    blend_scores(ai_ratings, det_result)      -> dict
    det_display(det)                          -> int  (±1000)

Constants exported:
    MODEL, COMPONENT_ORDER, PROMPT_SYSTEM
"""

import re
import time

from score import DEFAULT_WEIGHTS

MODEL           = "openai/gpt-oss-20b"
COMPONENT_ORDER = ["momentum", "valuation", "growth", "profitability", "risk", "technicals"]

PROMPT_SYSTEM = (
    "You are a senior equity research analyst at a top-tier investment bank. "
    "Produce only the structured analysis in the exact format shown — "
    "no preamble, no commentary outside the format."
)


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_prompt(facts, det, headlines=None):
    """Return the full user-turn prompt string for a given stock."""
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
        f"1 year:           {pct('yearTrendPct')}",
        f"vs 52w high:      {pct('distFrom52wHigh')}",
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
        f"Dividend yield:   {pct('dividendYieldPct')}",
        f"Current ratio:    {val('currentRatio', '.2f')}",
    ]))

    ref_line = (
        "  ".join(f"{k}={_det_norm(k):+d}" for k in COMPONENT_ORDER)
        + "  (algorithmic signal, -100..+100, reference only)"
    )

    news_section = ""
    if headlines:
        news_section = "\nRecent news:\n" + "\n".join(f"* {h}" for h in headlines) + "\n"

    company   = f.get("companyName", f.get("ticker", ""))
    ticker    = f.get("ticker", "")
    sector    = f.get("sector", "N/A")
    industry  = f.get("industry", "N/A")
    price_now = val("price", ".2f", "$")

    return f"""\
OUTPUT FORMAT — follow this EXACTLY. Begin your response with Line 1. No preamble.

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

EXAMPLE — your output must look exactly like this (different numbers/text for each stock):
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


# ── Groq API call ─────────────────────────────────────────────────────────────

def call_groq(client, prompt, spinner=None):
    """
    Stream the LLM response to a buffer.
    Returns (full_text, usage | None).

    spinner (optional): any object with .tick(n) and .message attribute.
      tick() is called per token chunk so the caller can update a progress
      display; .message is written during rate-limit countdowns.

    Auto-retries up to 3 times on Groq 429 / rate-limit errors.
    Falls back automatically if the model rejects reasoning_effort.
    """
    msgs = [
        {"role": "system", "content": PROMPT_SYSTEM},
        {"role": "user",   "content": prompt},
    ]

    use_reasoning = True          # flipped once if model rejects the param
    rate_attempts = 0
    RATE_WAITS    = [20, 45, 90]  # seconds to wait on successive rate-limit hits

    def _make_stream():
        kw = dict(
            model=MODEL, messages=msgs, temperature=0.9,
            max_completion_tokens=1500, top_p=1,
            stream=True, stop=None, seed=6767,
        )
        if use_reasoning:
            kw["reasoning_effort"] = "medium"
        return client.chat.completions.create(**kw)

    def _is_rate_limit(e):
        s = str(e).lower()
        return "rate limit" in s or "too many" in s or "429" in str(e)

    def _is_reasoning_err(e):
        s = str(e).lower()
        return any(x in s for x in (
            "reasoning_effort", "unsupported", "unknown field",
            "unexpected keyword", "invalid argument",
        ))

    def _countdown(seconds):
        for remaining in range(seconds, 0, -1):
            if spinner:
                spinner.message = f"Rate limited — retrying in {remaining}s ..."
            time.sleep(1)
        if spinner:
            spinner.message = "Generating analysis ..."

    while True:
        # ── Establish stream ─────────────────────────────────────────────────
        try:
            completion = _make_stream()
        except Exception as e:
            if _is_reasoning_err(e) and use_reasoning:
                use_reasoning = False
                continue
            if _is_rate_limit(e) and rate_attempts < len(RATE_WAITS):
                _countdown(RATE_WAITS[rate_attempts])
                rate_attempts += 1
                continue
            raise

        # ── Drain stream ─────────────────────────────────────────────────────
        full            = ""
        usage           = None
        stream_rate_hit = False
        try:
            for chunk in completion:
                # Groq puts token counts in x_groq on the final chunk
                try:
                    xg = getattr(chunk, "x_groq", None)
                    if xg is not None and getattr(xg, "usage", None) is not None:
                        usage = xg.usage
                except Exception:
                    pass
                # Guard against empty-choices usage chunks
                try:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta is None:
                        continue
                    piece = delta.content or ""
                except (AttributeError, IndexError):
                    continue
                if piece:
                    if spinner:
                        spinner.tick(len(piece.split()))
                    full += piece
        except Exception as e:
            if _is_rate_limit(e) and rate_attempts < len(RATE_WAITS):
                _countdown(RATE_WAITS[rate_attempts])
                rate_attempts += 1
                stream_rate_hit = True
            else:
                raise

        if stream_rate_hit:
            continue

        return full, usage


# ── Response parser ───────────────────────────────────────────────────────────

def parse_response(text):
    """
    Three-pass parser that tolerates LLM formatting drift.

    Pass 1 — line scan for exactly-6 comma-separated integers.
    Pass 2 — regex scan for any 6-integer sequence anywhere in the text.
    Pass 3 — look for individually labeled values (e.g. "momentum: 45").

    Returns a dict with keys:
        ratings       dict[str, int] — may be empty on total parse failure
        price_target  float | None
        explanation   str
        buy_reasons   list[str]  (up to 4)
        sell_reasons  list[str]  (up to 4)
    """
    # Strip reasoning/think blocks emitted by some models
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"</?think>", "", text).strip()

    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # ── Pass 1 ────────────────────────────────────────────────────────────────
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

    # ── Pass 2 ────────────────────────────────────────────────────────────────
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

    # ── Pass 3 ────────────────────────────────────────────────────────────────
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

    # ── BUY / SELL sections ───────────────────────────────────────────────────
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


# ── Score blending ────────────────────────────────────────────────────────────

def blend_scores(ai_ratings, det_result, weights=None):
    """
    Blend AI ratings (±100 per component) with the deterministic score.

    Returns a dict with:
        blended_components  dict[str, int]  ±100 each  (for bar charts)
        blended             int             ±1000       (60% AI + 40% det)
        ai_total            int             ±1000
        det_total           int             ±1000
        det_norm            dict[str, int]  ±100 each
        outlook             "BULLISH" | "NEUTRAL" | "BEARISH"
    """
    w = weights or DEFAULT_WEIGHTS

    # Normalise det breakdown values to ±100
    det_norm = {}
    for k in COMPONENT_ORDER:
        raw = det_result["breakdown"].get(k, 0)
        mx  = w.get(k, 0.1) * 1000
        det_norm[k] = int(round(max(-100, min(100, raw / mx * 100)))) if mx else 0

    # Per-component blend: 60% AI + 40% det
    blended_components = {
        k: int(round(max(-100, min(100, 0.6 * ai_ratings.get(k, 0) + 0.4 * det_norm.get(k, 0)))))
        for k in COMPONENT_ORDER
    }

    # Weighted totals → ±1000 (multiply before rounding for full granularity)
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
    """Return the deterministic score normalised to ±1000 for display."""
    dn = {
        k: max(-100, min(100, det["breakdown"].get(k, 0) / (DEFAULT_WEIGHTS.get(k, 0.1) * 1000) * 100))
        for k in COMPONENT_ORDER
        if DEFAULT_WEIGHTS.get(k, 0.1) * 1000
    }
    total_100 = sum(dn.get(k, 0) * DEFAULT_WEIGHTS.get(k, 0) for k in COMPONENT_ORDER)
    return int(round(max(-1000, min(1000, total_100 * 10))))
