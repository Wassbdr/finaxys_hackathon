"""Compare simulator stats with a LOBSTER sample.

python scripts/lobster_compare.py --message data/lobster/<ticker>_message_10.csv \
    --book data/lobster/<ticker>_orderbook_10.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from sentinel.sim.validate import comparison_markdown, load_lobster, lobster_stats, sim_stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", type=Path, required=True)
    parser.add_argument("--book", type=Path, required=True)
    parser.add_argument("--events", type=Path, default=Path("data/events.parquet"))
    parser.add_argument("--out", type=Path, default=Path("docs/lobster_comparison.md"))
    args = parser.parse_args()

    message, book = load_lobster(args.message, args.book)
    real = lobster_stats(message, book)
    sim = sim_stats(pd.read_parquet(args.events))
    table = comparison_markdown(sim, real)
    args.out.write_text("# Simulator vs. LOBSTER sample\n\n" + table, encoding="utf-8")
    print(table)


if __name__ == "__main__":
    main()
