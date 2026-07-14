"""
evaluation - forward-return eval harness for the stock scanner.

Answers the only question that matters: do the scores actually predict which
stocks go up? It reads the accumulated public scans (data/raw/*.jsonl), lines
each day's price up against later days' prices to get realized forward returns,
and measures how well each signal (blended / deterministic / AI-only) ranked
them ahead of time.

  panel   - load scans into aligned price + signal panels (with dedupe)
  metrics - rank IC, quantile spread, top-N long-only, price-target calibration
  report  - assemble + render the results

Run it:  python scripts/run_eval.py
"""

from .panel import Panel, load_panel, SIGNALS  # noqa: F401
