# AI Stock Evaluator

A terminal-based stock analysis tool that blends a deterministic scoring algorithm with Groq LLM reasoning into a single −1000 → +1000 score with BULLISH / NEUTRAL / BEARISH outlook.

---

## Features

- **Blended score** — 60 % AI (Groq LLM) + 40 % deterministic algorithm across six components: momentum, valuation, growth, profitability, risk, and technicals.
- **Price target** — LLM generates a 12-month fair-value estimate with upside/downside % vs current price.
- **Performance bars** — 1 d / 1 w / 1 mo / 3 mo / 1 yr price trend shown inline, scaled so small daily moves remain visible.
- **Buy & sell reasons** — structured bullet points from the LLM, colour-coded green / red.
- **Search history** — last 30 analyses accessible from the main menu (`[H]`).
- **Auto-retry on rate limits** — live countdown shown in the spinner when Groq's TPM cap is hit.
- **Token usage display** — prompt + completion token counts printed after each analysis.
- **JSONL log** — every run appended to `logs/searches.jsonl`; survives between sessions.

---

## Architecture

```
ai-stock-eval/
├── main.py        UI layer — menus, spinners, ANSI rendering, orchestration
├── engine.py      Pure analysis — prompt building, Groq call, response parsing, score blending
├── score.py       Deterministic 6-component scoring algorithm
├── fetch.py       Data fetching — yfinance (free) + optional Finnhub supplement
├── log.py         JSONL persistence helpers
├── requirements.txt
├── changelog.md
└── tests/
    └── test_score.py   28 pytest regression tests
```

---

## Setup

**1. Install dependencies**

```
py -m pip install -r requirements.txt
```

**2. Get a Groq API key**

Sign up at <https://console.groq.com> — free tier is sufficient for casual use.

**3. Run**

```
py main.py                    # interactive menu
py main.py AAPL               # analyse one ticker then drop to menu
```

Pass your Groq key when prompted, or set the environment variable:

```
set GROQ_API_KEY=gsk_...      # Windows
export GROQ_API_KEY=gsk_...   # macOS / Linux
```

**Optional: Finnhub key**

Finnhub supplements yfinance with more accurate real-time quotes and is used for news headlines in testing mode. A free key from <https://finnhub.io> works fine. All financial facts default to yfinance if no Finnhub key is supplied.

---

## Scoring

| Component     | Weight | What it measures |
|---------------|--------|-----------------|
| Momentum      | 25 %   | Price trend strength |
| Valuation     | 20 %   | PE / PB vs sector norms |
| Growth        | 20 %   | Revenue & EPS growth |
| Profitability | 15 %   | Net margin, ROE, ROIC |
| Risk          | 10 %   | Beta, debt, current ratio |
| Technicals    | 10 %   | 52-week position, volume |

Each component is rated −100 → +100 by both the LLM and the deterministic algorithm, then blended. The final score is the weighted sum scaled to −1000 → +1000.

---

## Testing

```
py -m pytest tests/ -q
```

28 regression tests covering edge cases, component directions, and snapshot comparisons.

---

## Changelog

See [changelog.md](changelog.md) for the full history.
