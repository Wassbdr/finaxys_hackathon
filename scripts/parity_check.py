"""Compare the Python and Java engines on a real event log with the trained models.

python scripts/parity_check.py [--events data/sim_seed9/events.parquet] [--n N]
Exit code 1 if any mismatch is found. Never used with the final test simulation.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from sentinel.stream.engine import load_engine_parts
from sentinel.stream.parity import compare_engines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, default=Path("data/sim_seed9/events.parquet"))
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    parser.add_argument("--n", type=int, default=None)
    parser.add_argument("--workdir", type=Path, default=Path("artifacts/parity"))
    args = parser.parse_args()
    if args.events.parent.name.startswith("test_"):
        sys.exit("refusing to run on the final test simulation")

    booster, thresholds, rule_sets = load_engine_parts(args.model_dir)
    report = compare_engines(pd.read_parquet(args.events), booster, thresholds, rule_sets, args.workdir, args.n)
    print(f"feature rows compared: {report.n_rows:,}; alerts (all detectors): {report.n_alerts:,}")
    if report.mismatches:
        print("PARITY FAILED:")
        print("\n".join(f"  - {m}" for m in report.mismatches))
        sys.exit(1)
    print("PARITY OK: identical feature rows, probabilities and alert sets")


if __name__ == "__main__":
    main()
