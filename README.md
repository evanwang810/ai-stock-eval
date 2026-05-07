# ai-stock-eval

stock evaluator for the terminal. throws a groq llm at your ticker plus a bunch of deterministic scoring logic and spits out a -1000 to +1000 score with buy/sell thesis.

## what it does

- blends 60% llm (groq) + 40% deterministic scoring across momentum, valuation, growth, profitability, risk, technicals
- gives you a 12-month price target
- shows 1d/1w/1mo/3mo/1yr performance bars
- explains the buy/sell case via the llm
- keeps a jsonl log of all runs so you don't have to re-ask
- auto-retries when you hit rate limits with a live countdown
- tells you how many tokens you burned

## files

```
main.py       terminal ui, menus, spinners, ansi colors
engine.py     the actual analysis logic (prompt, groq call, parsing, scoring)
score.py      deterministic scoring algorithm
fetch.py      pulls facts from yfinance, optionally finnhub
log.py        jsonl logging
tests/        pytest stuff (28 tests)
```

## setup

1. install deps
```
pip install -r requirements.txt
```

2. get a groq key from https://console.groq.com (free tier works)

3. run it
```
py main.py              # interactive
py main.py AAPL         # one ticker then menu
```

or set env var:
```
set GROQ_API_KEY=gsk_...
```

finnhub key is optional (supplement yfinance + news). get one free at https://finnhub.io

## how the scoring works

| component | weight | what it is |
|-----------|--------|-----------|
| momentum | 25% | how the price is moving |
| valuation | 20% | pe/pb vs normal for the sector |
| growth | 20% | revenue and earnings growth |
| profitability | 15% | margins, roe, roic |
| risk | 10% | beta, debt, liquidity |
| technicals | 10% | 52-week position, volume |

each gets -100 to +100 from both the llm and the algorithm, blends them, weighted sum = final score (-1000 to +1000)

## run tests

```
pytest tests/ -q
```

28 tests, all snapshot-based

see [changelog.md](changelog.md)
