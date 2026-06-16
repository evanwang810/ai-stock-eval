"""
score.py - 6-component deterministic scoring

ported from the js version, with sector pe thresholds

run it: py score.py
"""

# Tilted toward growth + momentum and away from cheap-and-stable. The old
# 0.20 valuation / 0.10 risk weighting rewarded low-PE, low-beta, dividend-payers
# (banks, utilities, staples) - stocks that rarely appreciate much. Growth and
# momentum are what actually drive big moves, so they dominate now. These weights
# apply to the *blended* score, so they curb the AI's value/bank tilt too.
DEFAULT_WEIGHTS = {
    "momentum":      0.20,
    "valuation":     0.10,
    "growth":        0.35,
    "profitability": 0.12,
    "risk":          0.08,
    "technicals":    0.15,
}

# ── Sector profiles ───────────────────────────────────────────────────────────
# Each sector has:
#   pe_cheap / pe_ok / pe_high / pe_very_high  - thresholds that replace the
#       flat breakpoints in the base algorithm (because a PE of 40 is normal
#       for high-growth tech but alarming for a utility)
#   growth_mult  - multiplier on the growth component score
#                  (growth matters more for tech than for utilities)
#   risk_note    - descriptive label used in future LLM context

SECTOR_PROFILES = {
    # yfinance sector string          pe thresholds          growth mult   label
    "Technology":            dict(pe_cheap=20, pe_ok=35, pe_high=60, pe_very_high=100, growth_mult=1.4, label="high-growth tech"),
    "Healthcare":            dict(pe_cheap=18, pe_ok=28, pe_high=50, pe_very_high=80,  growth_mult=1.2, label="healthcare/biotech"),
    "Communication Services":dict(pe_cheap=18, pe_ok=28, pe_high=45, pe_very_high=70,  growth_mult=1.1, label="media/telecom"),
    "Consumer Cyclical":     dict(pe_cheap=14, pe_ok=22, pe_high=35, pe_very_high=55,  growth_mult=1.1, label="consumer cyclical"),
    "Industrials":           dict(pe_cheap=14, pe_ok=22, pe_high=35, pe_very_high=55,  growth_mult=1.0, label="industrial"),
    "Consumer Defensive":    dict(pe_cheap=14, pe_ok=20, pe_high=30, pe_very_high=45,  growth_mult=0.8, label="consumer staples"),
    "Financial Services":    dict(pe_cheap=10, pe_ok=16, pe_high=25, pe_very_high=40,  growth_mult=0.7, label="financials"),
    "Real Estate":           dict(pe_cheap=20, pe_ok=35, pe_high=55, pe_very_high=80,  growth_mult=0.7, label="REIT/real estate"),
    "Utilities":             dict(pe_cheap=14, pe_ok=20, pe_high=28, pe_very_high=38,  growth_mult=0.6, label="utility"),
    "Energy":                dict(pe_cheap=10, pe_ok=16, pe_high=25, pe_very_high=40,  growth_mult=0.9, label="energy/cyclical"),
    "Basic Materials":       dict(pe_cheap=12, pe_ok=18, pe_high=28, pe_very_high=45,  growth_mult=0.9, label="materials/cyclical"),
}

# Default profile when sector is unknown
_DEFAULT_PROFILE = dict(pe_cheap=12, pe_ok=20, pe_high=30, pe_very_high=45, growth_mult=1.0, label="general")


def get_sector_profile(sector):
    """Return the sector profile for a given sector string (case-insensitive match)."""
    if not sector:
        return _DEFAULT_PROFILE
    for key, profile in SECTOR_PROFILES.items():
        if key.lower() in sector.lower() or sector.lower() in key.lower():
            return profile
    return _DEFAULT_PROFILE


# ── Helpers ───────────────────────────────────────────────────────────────────

def n(v):
    """True if v is a usable number (mirrors isNum in JS)."""
    try:
        f = float(v)
        return f == f and f not in (float("inf"), float("-inf"))
    except (TypeError, ValueError):
        return False


def clamp(val, lo, hi):
    return max(lo, min(hi, val))


# ── Main scoring function ─────────────────────────────────────────────────────

def compute_score(facts, weights=None):
    w = weights if weights is not None else DEFAULT_WEIGHTS

    # Sector profile - used to adjust PE thresholds and growth weighting
    profile = get_sector_profile(facts.get("sector", ""))

    # ── 1. MOMENTUM  (max raw ±560) ──────────────────────────────────────────
    mom = 0
    if n(facts.get("dayChangePct")):        mom += clamp(facts["dayChangePct"]       * 2.0,  -20,  20)
    if n(facts.get("weekTrendPct")):        mom += clamp(facts["weekTrendPct"]       * 4.0,  -55,  55)
    if n(facts.get("oneMonthTrendPct")):    mom += clamp(facts["oneMonthTrendPct"]   * 5.5,  -90,  90)
    if n(facts.get("threeMonthTrendPct")):  mom += clamp(facts["threeMonthTrendPct"] * 4.5, -110, 110)
    if n(facts.get("sixMonthTrendPct")):    mom += clamp(facts["sixMonthTrendPct"]   * 2.8,  -85,  85)
    if n(facts.get("yearTrendPct")):        mom += clamp(facts["yearTrendPct"]       * 2.2, -130, 130)
    # Trend consistency: steady accumulation beats a few lucky spikes
    if n(facts.get("upDayRatio")):
        udr = float(facts["upDayRatio"])
        if   udr > 0.62: mom += 40
        elif udr > 0.55: mom += 20
        elif udr < 0.38: mom -= 40
        elif udr < 0.45: mom -= 20
    # Momentum acceleration: is the recent month outpacing the prior quarter's run-rate?
    if n(facts.get("oneMonthTrendPct")) and n(facts.get("threeMonthTrendPct")):
        recent_pace = float(facts["oneMonthTrendPct"])
        prior_pace  = (float(facts["threeMonthTrendPct"]) - recent_pace) / 2  # per-month avg of months 2-3
        if   recent_pace > prior_pace + 4: mom += 30   # accelerating
        elif recent_pace < prior_pace - 4: mom -= 30   # decelerating
    mom_score = (clamp(mom, -560, 560) / 560) * 1000 * w["momentum"]

    # ── 2. VALUATION  (max raw ±320) - PE thresholds are sector-adjusted ─────
    val = 0
    if n(facts.get("peTTM")):
        pe = float(facts["peTTM"])
        p  = profile
        if   pe < 0:                val -= 90    # loss-making
        elif pe < p["pe_cheap"]:    val += 120   # genuinely cheap for this sector
        elif pe < p["pe_ok"]:       val +=  75   # fair value
        elif pe < p["pe_high"]:     val +=  25   # a bit stretched
        elif pe < p["pe_very_high"]:val -=  45   # expensive
        elif pe < p["pe_very_high"] * 1.5: val -= 95   # very expensive
        else:                       val -= 130   # extreme valuation

    if n(facts.get("pb")) and float(facts["pb"]) > 0:
        pb = float(facts["pb"])
        if   pb < 1.5: val += 50
        elif pb < 3:   val += 20
        elif pb > 10:  val -= 45
    if n(facts.get("epsTTM")):
        val += 40 if float(facts["epsTTM"]) > 0 else -40
    if n(facts.get("debtToEquity")):
        de = float(facts["debtToEquity"])
        if   de > 4: val -= 70
        elif de > 2: val -= 35
    val_score = (clamp(val, -320, 320) / 320) * 1000 * w["valuation"]

    # ── 3. GROWTH  (max raw ±310, then scaled by sector growth_mult) ─────────
    gro = 0
    if n(facts.get("revenueGrowthPct")):
        rg = float(facts["revenueGrowthPct"])
        if   rg > 40:  gro += 130
        elif rg > 20:  gro +=  85
        elif rg > 8:   gro +=  40
        elif rg > 0:   gro +=   5
        elif rg > -10: gro -=  55
        else:          gro -= 100
    if n(facts.get("epsGrowthTTMYoy")):
        eg = float(facts["epsGrowthTTMYoy"])
        if   eg > 30:  gro += 90
        elif eg > 10:  gro += 55
        elif eg > 0:   gro += 20
        elif eg > -15: gro -= 35
        else:          gro -= 70
    elif n(facts.get("epsGrowthQtrYoy")):
        qg = float(facts["epsGrowthQtrYoy"])
        gro += 40 if qg > 20 else 20 if qg > 5 else 5 if qg > 0 else -15 if qg > -15 else -35
    # Sector multiplier: growth matters more for tech, less for utilities
    gro_score = (clamp(gro, -310, 310) / 310) * 1000 * w["growth"] * profile["growth_mult"]

    # ── 4. PROFITABILITY  (max raw ±280) ──────────────────────────────────────
    pro = 0
    if n(facts.get("netMarginPct")):
        nm = float(facts["netMarginPct"])
        if   nm > 25: pro += 100
        elif nm > 15: pro +=  60
        elif nm > 5:  pro +=  20
        elif nm > 0:  pro -=   5
        else:         pro -=  80
    if n(facts.get("roeTTM")):
        roe = float(facts["roeTTM"])
        if   roe > 25: pro +=  55
        elif roe > 15: pro +=  30
        elif roe < 0:  pro -=  40
    if n(facts.get("roic")):
        roic = float(facts["roic"])
        if   roic > 20: pro +=  55
        elif roic > 10: pro +=  25
        elif roic < 0:  pro -=  35
    if n(facts.get("grossMarginPct")):
        gm = float(facts["grossMarginPct"])
        if   gm > 50: pro += 30
        elif gm > 30: pro += 10
        elif gm < 15: pro -= 20
    pro_score = (clamp(pro, -280, 280) / 280) * 1000 * w["profitability"]

    # ── 5. RISK / STABILITY  (max raw ±210) ───────────────────────────────────
    risk = 0
    if n(facts.get("beta")):
        beta = float(facts["beta"])
        if   beta < 0.6:  risk +=  90
        elif beta < 0.85: risk +=  55
        elif beta < 1.15: risk +=  15
        elif beta < 1.5:  risk -=  45
        elif beta < 2.0:  risk -=  80
        else:             risk -= 110
    if n(facts.get("dividendYieldPct")) and float(facts["dividendYieldPct"]) > 0:
        risk += clamp(float(facts["dividendYieldPct"]) * 12, 0, 55)
    if n(facts.get("currentRatio")):
        cr = float(facts["currentRatio"])
        if   cr > 2:   risk += 30
        elif cr > 1.2: risk += 10
        elif cr < 0.8: risk -= 35
    if n(facts.get("distFrom52wLow")) and float(facts["distFrom52wLow"]) < 10:
        risk -= 25
    # Realized volatility: calm tape is safer than a chainsaw chart
    if n(facts.get("volatilityAnnualPct")):
        vol = float(facts["volatilityAnnualPct"])
        if   vol < 20: risk += 35
        elif vol < 32: risk += 12
        elif vol > 60: risk -= 60
        elif vol > 45: risk -= 30
    risk_score = (clamp(risk, -270, 270) / 270) * 1000 * w["risk"]

    # ── 6. TECHNICALS  (max raw ±280) ─────────────────────────────────────────
    tech = 0
    if n(facts.get("distFrom52wHigh")):
        dfh = float(facts["distFrom52wHigh"])
        if   dfh > -3:   tech -= 70
        elif dfh > -10:  tech -= 25
        elif dfh < -45:  tech += 55
        elif dfh < -25:  tech += 25
    if n(facts.get("latestVolume")) and n(facts.get("avgVolume10d")) and float(facts["avgVolume10d"]) > 0:
        vr = float(facts["latestVolume"]) / float(facts["avgVolume10d"])
        if   vr > 2.0: tech += 35
        elif vr > 1.5: tech += 20
        elif vr < 0.5: tech -= 15
    # RSI(14): mean-reversion signal - oversold is opportunity, overbought is froth
    if n(facts.get("rsi14")):
        rsi = float(facts["rsi14"])
        if   rsi < 30: tech += 45
        elif rsi < 40: tech += 20
        elif rsi > 75: tech -= 45
        elif rsi > 65: tech -= 18
    # Trend structure vs moving averages
    if n(facts.get("pctVsSma50")):
        tech += 25 if float(facts["pctVsSma50"]) > 0 else -25
    if n(facts.get("pctVsSma200")):
        tech += 35 if float(facts["pctVsSma200"]) > 0 else -35
    # Golden cross (50d SMA above 200d) - the classic long-term trend confirmation
    gc = facts.get("goldenCross")
    if isinstance(gc, bool):
        tech += 25 if gc else -25
    tech_score = (clamp(tech, -280, 280) / 280) * 1000 * w["technicals"]

    total = int(round(clamp(mom_score + val_score + gro_score + pro_score + risk_score + tech_score, -1000, 1000)))

    return {
        "total": total,
        "sector_profile": profile["label"],    # so you can see which profile was applied
        "breakdown": {
            "momentum":      int(round(mom_score)),
            "valuation":     int(round(val_score)),
            "growth":        int(round(gro_score)),
            "profitability": int(round(pro_score)),
            "risk":          int(round(risk_score)),
            "technicals":    int(round(tech_score)),
        },
    }


# ── Run directly to test ───────────────────────────────────────────────────────
if __name__ == "__main__":
    import json

    # High-growth tech stock - PE of 35 should be fine here
    tech_stock = {
        "ticker": "EXAMPLE_TECH",
        "sector": "Technology",
        "dayChangePct": 1.2,
        "weekTrendPct": 3.0,
        "oneMonthTrendPct": 8.0,
        "threeMonthTrendPct": 20.0,
        "yearTrendPct": 50.0,
        "peTTM": 35,           # would score neutral for a utility, fine for tech
        "pb": 6.0,
        "epsTTM": 4.0,
        "revenueGrowthPct": 25.0,
        "epsGrowthTTMYoy": 30.0,
        "netMarginPct": 20.0,
        "roeTTM": 30.0,
        "roic": 22.0,
        "grossMarginPct": 65.0,
        "beta": 1.3,
        "currentRatio": 2.0,
        "distFrom52wHigh": -12.0,
        "distFrom52wLow": 45.0,
        "latestVolume": 5e6,
        "avgVolume10d": 4e6,
    }

    # Same PE of 35 for a utility - should score much worse on valuation
    utility_stock = {**tech_stock, "ticker": "EXAMPLE_UTIL", "sector": "Utilities", "beta": 0.5, "dividendYieldPct": 3.5}

    print("=== Tech stock (PE 35 is normal) ===")
    r1 = compute_score(tech_stock)
    print(json.dumps(r1, indent=2))

    print("\n=== Same numbers, Utilities sector (PE 35 is expensive) ===")
    r2 = compute_score(utility_stock)
    print(json.dumps(r2, indent=2))

    print(f"\nValuation difference: tech={r1['breakdown']['valuation']:+d}  utility={r2['breakdown']['valuation']:+d}")
    print(f"Growth difference:    tech={r1['breakdown']['growth']:+d}  utility={r2['breakdown']['growth']:+d}  (growth_mult matters here)")
