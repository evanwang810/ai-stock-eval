# using ai-stock-eval as a library

the project is built to be imported. main.py is just the terminal UI. you can use the core modules for your own projects.

## modules

### engine.py - analysis pipeline

the main analysis logic. no ui, no ansi codes.

```python
from engine import build_prompt, call_groq, parse_response, blend_scores, det_display, MODEL
from groq import Groq

# get financial facts (from fetch.py or your own source)
facts = {
    "ticker": "AAPL",
    "price": 189.5,
    "peTTM": 28.5,
    # ... more fields
}

# get deterministic score (from score.py)
det_result = compute_score(facts)

# build a prompt for the llm
prompt = build_prompt(facts, det_result, headlines=None)

# call groq with your api key
client = Groq(api_key="your_groq_key")
raw_response, usage = call_groq(client, prompt, spinner=None)

# parse the response
parsed = parse_response(raw_response)
# parsed = {
#     "ratings": {"momentum": 45, "valuation": -20, ...},
#     "price_target": 210.0,
#     "explanation": "...",
#     "buy_reasons": [...],
#     "sell_reasons": [...]
# }

# blend ai + deterministic scores
blended = blend_scores(parsed["ratings"], det_result)
# blended = {
#     "blended": 285,           # final -1000 to +1000 score
#     "blended_components": {...},
#     "outlook": "BULLISH",
#     ...
# }

print(f"Score: {blended['blended']} ({blended['outlook']})")
print(f"Price target: ${parsed['price_target']}")
```

**exports:**
- `build_prompt(facts, det, headlines=None)` -> str
- `call_groq(client, prompt, spinner=None)` -> (str, usage | None)
- `parse_response(text)` -> dict
- `blend_scores(ai_ratings, det_result, weights=None)` -> dict
- `det_display(det)` -> int (±1000)
- `MODEL` (string)
- `COMPONENT_ORDER` (list)
- `PROMPT_SYSTEM` (string)

### score.py - deterministic scoring

no llm, pure math. useful on its own.

```python
from score import compute_score, DEFAULT_WEIGHTS

facts = {
    "ticker": "AAPL",
    "price": 189.5,
    "peTTM": 28.5,
    "revenueGrowthPct": 8.2,
    "roeTTM": 95.5,
    # ... more fields
}

result = compute_score(facts)
# result = {
#     "breakdown": {
#         "momentum": 250,
#         "valuation": -150,
#         "growth": 180,
#         ...
#     },
#     "total": 1234
# }

# use custom weights
custom_weights = {
    "momentum": 0.3,
    "valuation": 0.3,
    "growth": 0.2,
    "profitability": 0.1,
    "risk": 0.05,
    "technicals": 0.05,
}
result = compute_score(facts, custom_weights)
```

**exports:**
- `compute_score(facts, weights=None)` -> dict
- `DEFAULT_WEIGHTS` (dict)
- `SECTOR_PROFILES` (dict of sector thresholds)

### fetch.py - data fetching

pull facts from yfinance, optional finnhub.

```python
from fetch import fetch_facts, fetch_finnhub_news

# just yfinance (free, no key needed)
facts = fetch_facts("AAPL")

# yfinance + finnhub supplement (more accurate quotes)
facts = fetch_facts("AAPL", finnhub_key="your_finnhub_key")

# news headlines (finnhub only)
headlines = fetch_finnhub_news("AAPL", api_key="your_finnhub_key", n=5)
```

**exports:**
- `fetch_facts(symbol, finnhub_key=None)` -> dict
- `fetch_finnhub_news(symbol, api_key, n=5)` -> list[str]

### log.py - persistence

jsonl logging for runs.

```python
from log import save_log, load_logs

# save a record
save_log({
    "ticker": "AAPL",
    "success": True,
    "blended": 285,
    "outlook": "BULLISH",
    "price_target": 210.0,
})

# load last n runs
recent = load_logs(n=50)
for entry in recent:
    print(f"{entry['ts']} {entry['ticker']} {entry.get('blended', 'failed')}")
```

**exports:**
- `save_log(entry: dict)` -> None
- `load_logs(n: int = 50)` -> list[dict]

## example: build a batch analyzer

```python
from engine import build_prompt, call_groq, parse_response, blend_scores
from score import compute_score
from fetch import fetch_facts
from log import save_log
from groq import Groq

def analyze_ticker(symbol, groq_key):
    """analyze a single ticker, log it, return the result"""
    try:
        # fetch facts
        facts = fetch_facts(symbol)
        if not facts:
            return {"error": "no data"}
        
        # deterministic score
        det = compute_score(facts)
        
        # llm analysis
        client = Groq(api_key=groq_key)
        prompt = build_prompt(facts, det)
        raw, usage = call_groq(client, prompt)
        parsed = parse_response(raw)
        
        # blend scores
        blended = blend_scores(parsed["ratings"], det)
        
        # log it
        result = {
            "ticker": symbol,
            "success": True,
            "blended": blended["blended"],
            "outlook": blended["outlook"],
            "price_target": parsed["price_target"],
        }
        save_log(result)
        return result
    
    except Exception as e:
        result = {
            "ticker": symbol,
            "success": False,
            "error": str(e),
        }
        save_log(result)
        return result

# batch run
tickers = ["AAPL", "MSFT", "GOOGL", "TSLA"]
groq_key = "your_groq_key"

for ticker in tickers:
    result = analyze_ticker(ticker, groq_key)
    if result["success"]:
        print(f"{ticker}: {result['blended']} ({result['outlook']})")
    else:
        print(f"{ticker}: error - {result['error']}")
```

## example: use in a daily scheduler

```python
# my_watchlist_tracker.py
import os
import time
from datetime import datetime
from engine import build_prompt, call_groq, parse_response, blend_scores
from score import compute_score
from fetch import fetch_facts
from log import save_log, load_logs
from groq import Groq

# your watchlist
WATCHLIST = ["AAPL", "MSFT", "TSLA", "NVDA"]

def run_daily_check():
    groq_key = os.environ.get("GROQ_API_KEY")
    client = Groq(api_key=groq_key)
    
    results = []
    for symbol in WATCHLIST:
        try:
            facts = fetch_facts(symbol)
            det = compute_score(facts)
            prompt = build_prompt(facts, det)
            raw, _ = call_groq(client, prompt)
            parsed = parse_response(raw)
            blended = blend_scores(parsed["ratings"], det)
            
            results.append({
                "ticker": symbol,
                "score": blended["blended"],
                "outlook": blended["outlook"],
                "price": facts.get("price"),
            })
            
            save_log({
                "ticker": symbol,
                "success": True,
                "blended": blended["blended"],
                "outlook": blended["outlook"],
            })
            
            time.sleep(2)  # rate limit friendly
        except Exception as e:
            print(f"error analyzing {symbol}: {e}")
            save_log({"ticker": symbol, "success": False, "error": str(e)})
    
    # print summary
    print(f"\n{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    for r in results:
        print(f"  {r['ticker']:<6} {r['score']:>5} {r['outlook']:<10} ${r['price']:.2f}")

if __name__ == "__main__":
    run_daily_check()
```

then run daily via cron/windows task scheduler:
```
python my_watchlist_tracker.py
```

## notes

- `engine.py` doesn't import main.py, so no terminal/ansi stuff bleeds in
- all modules work standalone - you can use score.py without fetch.py or groq
- the `spinner` parameter in `call_groq()` is optional; pass None if you don't need it, or pass any object with `.tick(n)` and `.message` attributes
- rate limits: groq is typically 10k-30k tokens per minute on free tier. the engine retries automatically
