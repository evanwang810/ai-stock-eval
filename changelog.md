# Changelog — AI Stock Evaluator

All notable changes to this project. Dates are in YYYY-MM-DD format.

---

## [Unreleased]

_Changes staged but not yet assigned a version._

---

## [1.2.0] — 2026-05-01

### Added
- **Price target** — the LLM now generates a 12-month fair-value price target
  (`PRICE TARGET: $NNN`) which appears inline with the score, including upside/downside % vs current price.
- **Search history** — `[H]` from the main menu shows the last 30 searches with
  timestamp, ticker, blended score, outlook, and company name. Failures also shown.
- **ASCII art banner** — "STOCK" rendered in box-drawing block characters at startup,
  inside a bordered panel showing model name and testing-mode status.
- **Main menu** — `[A]` Analyze / `[H]` History / `[Q]` Quit shown at the root prompt
  instead of jumping directly to ticker input.
- **Visual score bars** — each category score now has a centred ASCII bar
  (`█` fill, `░` empty) that visually represents direction and magnitude.
- **Easter egg** — entering ticker `67` triggers a "data corruption" sequence:
  spinning noise, rapid random-character flood (~5 terminal pages), screen clear,
  then a ASCII-art jumpscare. The 67 is a reference to the hardcoded `seed=6767`
  used in all Groq calls. Entering `67` at the post-result prompt also works.

### Changed
- **Score normalisation** — all component scores now live on a unified −100..100
  scale. Previously the deterministic breakdown was weighted (e.g. momentum max ±250)
  and not directly comparable to AI's −100..100. Now:
  1. Each det component is normalised: `det_norm[k] = det_breakdown[k] / (weight[k] × 1000) × 100`, clamped to −100..100.
  2. Per-component blend: `blended[k] = 0.6 × ai[k] + 0.4 × det_norm[k]`.
  3. Final score: weighted average of blended components → also −100..100.
  4. Outlook thresholds adjusted: BULLISH > +15, BEARISH < −15.
  5. `fmt_score()` colour thresholds updated for the new scale (bold green >30, green >10, etc.).
- **Prompt overhaul** — the system prompt is now framed as a senior equity research
  analyst task. Key improvements:
  - Explicit dimension definitions added (momentum = price trend strength, etc.).
  - Output format section now shows a concrete example line for the ratings.
  - `PRICE TARGET:` added as its own mandatory output line.
  - Reduced token waste: removed "do not include any text outside your answer" in
    favour of placing format spec at the end, which models respect more reliably.
  - `temperature` lowered from 1.0 to 0.9 for slightly more consistent output.
  - Algorithmic reference line now shows normalised −100..100 values instead of
    raw weighted scores, making it more interpretable for the model.
- **Component display** — switched from a broken two-column layout (ANSI escape
  codes made `str.ljust()` mis-pad) to a single-column layout with coloured score
  + visual bar per category. Root cause: `f"{ansi_str:<N}"` counts invisible ANSI
  bytes toward N. Fixed everywhere via `ansi_ljust(s, width)` helper that strips
  escape codes before computing pad width.
- **`print_more()` table** — now shows three columns: AI | Det(norm) | Blended,
  plus weight, making it easy to see exactly how the blend was computed.
- **Parse robustness** — `parse_response()` improvements:
  - Strips markdown delimiters (`\``, `*`, `_`, `[`, `]`) before attempting to
    parse the ratings line.
  - Accepts decimal values from the model (e.g. `45.0`) via `round(float(...))`.
  - Two-pass fallback: if line-by-line parse fails, regex-scans the full text for
    any 6-integer sequence.
  - Price target extracted with its own regex before the explanation is assembled,
    so it never bleeds into the thesis text.
  - Raw output printed to terminal on parse failure to aid debugging.

### Fixed
- **Category scores showing same value for both columns** — the old two-column
  layout used `f"{fmt_score(v):<20}"` which is broken for ANSI-coloured strings;
  ANSI bytes inflate the apparent length so the padding was consistently wrong.
  Replaced with single-column + bar layout (no padding needed).

---

## [1.1.0] — 2026-04-30

### Added
- **Testing mode** — `IS_TESTING`, `TESTING_GROQ_KEY`, `TESTING_FINNHUB_KEY`
  constants at the top of `main.py`. When enabled, keys are baked in so the tool
  runs without CLI arguments.
- **Finnhub news** — `fetch_finnhub_news(symbol, api_key, n=5)` in `fetch.py`
  hits `/company-news` for the last 30 days and returns up to 5 deduplicated
  headlines. Only called in testing mode; Finnhub is never used to supplement
  yfinance facts in this mode.
- **Live token counter** — spinner now shows a running word-count while the LLM
  streams its response, giving visual feedback on generation progress.
- **Logging** — every search appends a JSON record to `logs/searches.jsonl`
  (created on first run). Fields: `ts`, `ticker`, `company`, `success`, `blended`,
  `ai_total`, `det_total`, `outlook`, `sector`, `price_target`, or `error`.
- **`[M]` More details** — post-result option that appends a full breakdown:
  component table, raw financial data in two columns, sector profile.
- **Interactive ticker input** — if no ticker is passed on the CLI, the tool
  prompts interactively instead of defaulting to AAPL.
- **`[T]` / `[Q]` post-result menu** — after displaying results the user can
  try another ticker or quit without restarting.

### Changed
- **No raw streaming** — LLM output is streamed to a buffer silently rather than
  printing tokens as they arrive. This allows full formatting control and avoids
  interleaving with the spinner. The spinner now shows a token count instead.
- **Colourful terminal UI** — ANSI colours throughout: cyan headings, green
  positive scores / buy reasons, red negative scores / sell reasons, yellow
  neutral, grey secondary text. `os.system("")` enables virtual terminal
  processing on Windows.
- **Token savings in prompt** — numeric values in the stats block reduced from
  2 decimal places to 1 (e.g. `$189.3` instead of `$189.30`). Saves ~20–30
  tokens per analysis with no loss of information.
- **`on_first_token` callback removed** — replaced by streaming-to-buffer approach;
  spinner stops when `call_groq()` returns instead of on first token.

### Fixed
- **`dividendYieldPct` 100× too high** — yfinance returns `dividendYield` as
  already-a-percentage (0.38 = 0.38%), unlike other metrics that are true decimals.
  Changed `_safe(info, "dividendYield", 100)` → `_safe(info, "dividendYield")`.
- **Windows Unicode crash** — score bar previously used `█` which Windows cp1252
  terminal can't encode. Added try/except with ASCII fallback (`#` and `.`).

---

## [1.0.0] — 2026-04-29  _(initial Python port)_

### Added
- **`score.py`** — deterministic 6-component scoring algorithm ported from
  `js/ai.js computeScore()`. Components: momentum (25%), valuation (20%),
  growth (20%), profitability (15%), risk (10%), technicals (10%).
- **Sector-aware PE thresholds** — 11 `SECTOR_PROFILES` with per-sector
  `pe_cheap / pe_ok / pe_high / pe_very_high` and `growth_mult` (tech 1.3×,
  utility 0.6×). Prevents penalising a tech stock for a "high" PE that is
  normal for its sector.
- **`fetch.py`** — yfinance as free unlimited primary source; optional Finnhub
  supplement for the web GUI path. Key fix: `debtToEquity` is returned by
  yfinance as a percentage (150 = 1.5×), so we divide by 100.
- **`main.py`** — Groq LLM integration with streaming, `reasoning_effort="medium"`,
  `seed=6767`. Auto-retry without `reasoning_effort` if the model doesn't support it.
  60% AI + 40% deterministic blending (same ratio as the JS front-end).
- **`parse_response()`** — extracts ratings, explanation, buy/sell reasons from
  the LLM's structured output. Strips `<think>` blocks for reasoning models.
- **`tests/test_score.py`** — 28 pytest regression tests covering: edge cases
  (empty facts, bounds, custom weights), each component direction, and 4 snapshot
  tests (AAPL-like, value trap, high-growth SaaS, stable utility). All green.

---

## Version scheme

`MAJOR.MINOR.PATCH`
- **MAJOR** — breaking change to CLI interface or output format
- **MINOR** — new feature or significant behaviour change
- **PATCH** — bug fix or documentation update
