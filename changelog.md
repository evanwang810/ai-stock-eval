# changelog

changes to ai-stock-eval. dates are YYYY-MM-DD.

## [Unreleased]

stuff that's done but not released yet

## [1.2.0] - 2026-05-01

### Added
- **Price target** - llm generates a 12-month fair value estimate with upside/downside %
- **Search history** - `[H]` shows the last 30 runs with timestamp, ticker, score, outlook
- **ASCII banner** - "STOCK" in box-drawing chars on startup
- **Main menu** - `[A]` Analyze / `[H]` History / `[Q]` Quit
- **Score bars** - ascii bars (█/░) for each component showing direction and magnitude
- **Easter egg** - enter ticker `67` for a data corruption jumpscare (seed=6767 reference)

### Changed
- **Score scale** - all components now unified to -100..+100 (was weighted/incomparable)
  - det components: `det_norm[k] = det_breakdown[k] / (weight[k] * 1000) * 100`
  - per-component: 60% ai + 40% det
  - final score: weighted average
  - outlook: BULLISH > +15, BEARISH < -15
- **Prompt rewrite** - framed as senior equity research analyst
  - explicit dimension definitions
  - `PRICE TARGET:` is mandatory on its own line
  - temp down from 1.0 to 0.9 for stability
  - algorithmic reference shows -100..+100 values instead of raw weighted
- **Component display** - fixed two-column ansi padding bug (was counting escape codes)
  - now single-column + bars per category
  - used `ansi_ljust()` helper to strip codes before measuring width
- **`print_more()` table** - now shows AI | Det(norm) | Blended + weight
- **Parser improvements**
  - strips markdown delimiters before parsing ratings
  - accepts decimals from model (45.0 becomes 45)
  - multi-pass fallback with regex scan
  - price target extracted separately so it doesn't bleed into thesis

### Fixed
- **Two-column ansi padding** - was counting invisible escape codes toward string width, broke everything

## [1.1.0] - 2026-04-30

### Added
- **Testing mode** - `IS_TESTING` constant with baked-in keys for dev
- **Finnhub news** - pulls last 30 days of headlines (testing only)
- **Live token counter** - spinner shows word count while llm streams
- **Logging** - every run saved to `logs/searches.jsonl` with timestamp, score, outlook
- **`[M]` More details** - full breakdown, raw financial data, sector profile
- **Interactive ticker** - prompts if no ticker on cli
- **`[T]` / `[Q]` menu** - continue or quit after results

### Changed
- **No raw streaming** - responses go to buffer silently, then formatted
- **Terminal colors** - ansi colors (cyan/green/red/yellow/grey)
- **Token savings** - reduced decimals in stats (189.3 not 189.30)
- **Removed `on_first_token` callback** - replaced with streaming-to-buffer

### Fixed
- **`dividendYieldPct` 100x too high** - yfinance already gives as %, not decimal
- **Windows unicode crash** - added ascii fallback for box chars on cp1252

## [1.0.0] - 2026-04-29

initial python port

### Added
- **score.py** - ported 6-component scoring algo from js/ai.js
  - momentum 25%, valuation 20%, growth 20%, profitability 15%, risk 10%, technicals 10%
  - sector-aware pe thresholds
- **fetch.py** - yfinance (free) + optional finnhub
- **main.py** - groq llm integration with streaming, seed=6767
- **parse_response()** - extracts ratings, explanation, buy/sell reasons
- **tests/test_score.py** - 28 pytest tests

## Version scheme

MAJOR.MINOR.PATCH
- MAJOR: breaking change to cli or output
- MINOR: new feature or significant behavior change
- PATCH: bug fix or docs
