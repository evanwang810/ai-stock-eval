# ai-stock-eval

Terminal stock evaluator. It feeds a ticker's fundamentals, price action, computed technicals, and recent news into an LLM, blends that with a deterministic scoring algorithm, and prints a **−1000 to +1000** score with a buy/sell thesis and a 12-month price target. Ships with a headless **watcher** for unattended daily scans you can run on a server or GitHub Actions.

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Installation](#installation)
- [Environment variables](#environment-variables)
- [Running it locally](#running-it-locally)
- [The watcher (daily scans)](#the-watcher-daily-scans)
- [Historical data & news](#historical-data--news)
- [Deployment](#deployment)
  - [Option A - GitHub Actions (free, no server)](#option-a--github-actions-free-no-server)
  - [Option B - a real server (systemd)](#option-b--a-real-server-systemd)
- [How the scoring works](#how-the-scoring-works)
- [Project layout](#project-layout)
- [Tests](#tests)

## What it does

- Blends **60% LLM + 40% deterministic** scoring across momentum, valuation, growth, profitability, risk, technicals.
- Computes its own technicals from price history - RSI(14), 50/200-day SMAs, golden cross, annualized volatility, trend consistency - with **no extra API calls**.
- Pulls recent **news headlines** so the model can read investor sentiment.
- Rotates across **multiple API keys** (round-robin + auto-failover on rate limits).
- Logs every run to JSONL so you can review history.

## Requirements

- **Python 3.10+** (3.12 recommended).
- At least one **LLM API key**. Default provider is [Cerebras](https://cloud.cerebras.ai) - free, no credit card, and you can create several keys to widen your rate limit. [Groq](https://console.groq.com) and [Google Gemini](https://aistudio.google.com) also work.
- A [Finnhub](https://finnhub.io) key (free) for news headlines / sentiment.
- *(Optional)* An [Alpha Vantage](https://www.alphavantage.co/support/#api-key) key for historical-price backtests.

## Installation

```bash
git clone https://github.com/evanwang810/ai-stock-eval.git
cd ai-stock-eval

python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

Then create your `.env`:

```bash
cp .env.example .env     # Windows: copy .env.example .env
```

Open `.env` and paste your key(s) into `LLM_API_KEYS`. That's the only required value.

## Environment variables

All configuration is via environment variables. Put them in a `.env` file (auto-loaded) or export them in your shell. See [.env.example](.env.example).

| Variable | Required | Default | What it's for |
|----------|----------|---------|---------------|
| `LLM_API_KEYS` | **yes** | - | One or more API keys, comma-separated. Rotated round-robin to spread rate-limit load. |
| `LLM_PROVIDER` | no | `cerebras` | `cerebras` \| `groq` \| `gemini`. |
| `LLM_MODEL` | no | provider default | Override the model (e.g. `gpt-oss-120b`). |
| `FINNHUB_KEY` | **yes** | - | News headlines → sentiment for the model. Free key at finnhub.io. |
| `ALPHAVANTAGE_API_KEY` | no | - | Used by `history.py` for historical prices (backtests). |
| `TICKERS` | no | top-100 list | Watcher only (the terminal app ignores it). Overrides the default top-100 list in `watcher/config.example.json`. |

Provider defaults: Cerebras → `gpt-oss-120b`, Groq → `openai/gpt-oss-120b`, Gemini → `gemini-2.5-flash`.

> The `openai` package is only the HTTP client - all three providers speak the OpenAI-compatible API. You do **not** need an OpenAI account.

**Setting env vars without a `.env` file:**

```powershell
# PowerShell
$env:LLM_API_KEYS = "csk-aaa,csk-bbb"
$env:LLM_PROVIDER = "cerebras"
```
```bash
# bash / zsh
export LLM_API_KEYS="csk-aaa,csk-bbb"
export LLM_PROVIDER="cerebras"
```

## Running it locally

```bash
python main.py              # interactive menu
python main.py AAPL         # analyze one ticker, then drop into the menu
```

Inside the menu: `A` analyze, `H` history, `Q` quit. After a result: `M` for the extended breakdown, `T` to try another ticker.

Example session:

```
$ python main.py NVDA
  ↳ NVIDIA Corporation  Technology · Semiconductors
  ↳ Det signal  +312  /1000  (high-growth tech)
  ↳ 5 recent headlines
  ↳ 1921 tokens
  ...
  Score  +266  /1000   BULLISH   Target  $185  (+12.4%)
```

### Local development

- The deterministic scorer ([score.py](score.py)) is pure and testable - run `pytest tests/ -q`. No keys needed.
- Each module runs standalone for quick checks:
  ```bash
  python score.py        # scoring demo (tech vs utility)
  python fetch.py AAPL   # dump the facts dict + technicals
  python history.py AAPL 2020-01-01 2021-01-01
  ```
- The LLM layer is isolated in [llm.py](llm.py). To add a provider, add one entry to `PROVIDERS` - nothing else changes.

## The watcher (daily scans)

Runs unattended: scans a ticker list every N hours, skips US trading hours, retries when the model misbehaves, writes timestamped JSON to `watcher/results/`.

```bash
cp watcher/config.example.json watcher/config.json   # then edit "tickers"
python watcher/watcher.py            # run forever
python watcher/watcher.py --once     # single cycle then exit (for cron / CI)
```

Keys come from your env automatically. `config.json` controls tickers, interval, retries, and the market-hours guard - see [watcher/config.example.json](watcher/config.example.json). The `TICKERS` env var overrides the config's ticker list (handy in CI).

## Historical data & news

- **News** ([fetch.py](fetch.py)): recent headlines are pulled via `FINNHUB_KEY` and handed to the model to weigh sentiment. Required.
- **Daily archive**: the watcher writes every per-ticker record to `data/raw/<date>.jsonl` (yfinance facts, scores, full LLM output, headline count). Headline *text* is split into `data/private/<date>.jsonl` (gitignored) and uploaded by the workflow as a 90-day private artifact - kept off the public repo to respect Finnhub's ToS. Download via the Actions tab and join on `(ticker, ts)` for a complete training record.
- **History** ([history.py](history.py)): `ALPHAVANTAGE_API_KEY` enables historical daily OHLCV from Alpha Vantage - a second source besides yfinance, for backtests. Free tier is ~25 requests/day, so cache aggressively. Note that *point-in-time fundamentals* (old P/E, margins) are a paid-data problem; free sources only give clean historical **prices**.

## Deployment

A once-a-day scan doesn't need a 24/7 server - it needs a scheduler. Two paths:

### Option A - GitHub Actions (free, no server)

The included workflow [`.github/workflows/watcher.yml`](.github/workflows/watcher.yml) runs one scan per night and commits results back to the repo. No card, no VM.

**Step by step:**

1. **Push this repo to GitHub** (see the bottom of this README, or it's already done if you cloned it).
2. **Add your keys as a secret.** Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:
   - Name: `LLM_API_KEYS`  ·  Value: `csk-aaa,csk-bbb,csk-ccc`
   - `FINNHUB_KEY` for news (required).
3. **Set the tickers as a variable.** Same page → **Variables** tab → **New repository variable**:
   - *(optional)* `TICKERS` to override the default top-100 list, e.g. `AAPL,MSFT,NVDA`
   - *(optional)* `LLM_PROVIDER` (default `cerebras`), `LLM_MODEL`.
4. **Give the workflow write access** so it can commit results: repo → **Settings** → **Actions** → **General** → **Workflow permissions** → select **Read and write permissions** → Save.
5. **Adjust the schedule** if you want. In `watcher.yml`, the `cron` is `0 7 * * *` (07:00 UTC ≈ 02:00 ET, market closed). [Cron syntax reference](https://crontab.guru).
6. **Test it now:** repo → **Actions** tab → **daily-stock-watch** → **Run workflow**. Results land in `watcher/results/` and `logs/searches.jsonl`, committed by `stock-watcher-bot`.

The [`ci.yml`](.github/workflows/ci.yml) workflow runs the test suite on every push/PR - no secrets required.

### Option B - a real server (systemd)

For a long-running process (e.g. an always-on VPS):

```ini
# /etc/systemd/system/stock-watcher.service
[Unit]
Description=ai-stock-eval watcher
After=network-online.target

[Service]
WorkingDirectory=/opt/ai-stock-eval
EnvironmentFile=/opt/ai-stock-eval/.env
ExecStart=/opt/ai-stock-eval/.venv/bin/python watcher/watcher.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now stock-watcher
journalctl -u stock-watcher -f      # tail the logs
```

Or, dead simple: `nohup python watcher/watcher.py > watcher.log 2>&1 &`.

## How the scoring works

| Component | Weight | What it measures |
|-----------|--------|------------------|
| momentum | 25% | price trend, consistency, acceleration |
| valuation | 20% | P/E, P/B vs sector norms |
| growth | 20% | revenue & earnings growth |
| profitability | 15% | margins, ROE, ROIC |
| risk | 10% | beta, debt, liquidity, volatility |
| technicals | 10% | 52w position, volume, RSI, SMA structure |

Each component gets −100…+100 from both the LLM and the algorithm. They're blended (60/40), then a weighted sum produces the final −1000…+1000 score. P/E thresholds are sector-adjusted (a P/E of 35 is fine for tech, alarming for a utility).

## Project layout

```
main.py            terminal UI - menus, spinners, ANSI rendering
llm.py             swappable provider layer: clients, key pool, the LLM call
engine.py          analysis logic: prompt building, parsing, score blending
score.py           deterministic scoring algorithm (pure, tested)
fetch.py           facts + computed technicals from yfinance; Finnhub news
history.py         historical prices via Alpha Vantage (backtests)
log.py             JSONL run logging
watcher/           headless daily scanner (server / GitHub Actions)
  watcher.py
  config.example.json
.github/workflows/ ci.yml (tests) + watcher.yml (scheduled scans)
tests/             pytest suite for the scorer
.env.example       documented environment template
```

## Tests

```bash
pytest tests/ -q
```

See [changelog.md](changelog.md).
