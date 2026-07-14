"""
run_eval.py - grade the scanner's track record against realized returns.

Reads the accumulated public scans and prints a scorecard: rank IC, quantile
buckets, top-N excess, and price-target calibration for each signal. Optionally
dumps the raw metrics as JSON (--json) for the site or further analysis.

  python scripts/run_eval.py
  python scripts/run_eval.py --horizons 1 3 5 --top 25
  python scripts/run_eval.py --json data/eval/latest.json
"""

import argparse
import json
import sys
from pathlib import Path

# allow `python scripts/run_eval.py` from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.panel import load_panel                # noqa: E402
from evaluation.report import build, render_text       # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="forward-return eval for the stock scanner")
    ap.add_argument("--horizons", type=int, nargs="+", default=[1, 2, 3],
                    help="forward horizons in scans (default: 1 2 3)")
    ap.add_argument("--top", type=int, default=20, help="top-N picks to grade (default 20)")
    ap.add_argument("--quantiles", type=int, default=5, help="quantile buckets (default 5)")
    ap.add_argument("--json", metavar="PATH", help="also write raw metrics to this JSON file")
    args = ap.parse_args(argv)

    panel = load_panel()
    if len(panel.dates) < 2:
        print("Not enough distinct scans to evaluate yet "
              f"({len(panel.dates)} found). Come back after a few more days.",
              file=sys.stderr)
        return 1

    result = build(panel, args.horizons, topn=args.top, q=args.quantiles)
    print(render_text(result))

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"\n[wrote {out}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
