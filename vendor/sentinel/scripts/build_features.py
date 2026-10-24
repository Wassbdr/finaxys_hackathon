"""Build (or rebuild) the cached feature table for a simulation directory.

python scripts/build_features.py data/sim_seed7 [--rebuild]
Test directories (test_*) are refused: they are loaded only via load_sim(final=True).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from sentinel.detect.dataset import load_sim


def main() -> None:
    """Parse arguments, build the feature cache and print a one-line summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sim_dir", type=Path)
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    df = load_sim(args.sim_dir, rebuild=args.rebuild)
    print(f"{args.sim_dir}: {len(df):,} trader-windows, {(df.label != 'NORMAL').sum():,} manipulation windows")


if __name__ == "__main__":
    main()
