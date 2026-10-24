"""Generate the Sentinel dataset: python scripts/generate_data.py --out data"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from sentinel.sim.labels import write_dataset
from sentinel.sim.runner import SimConfig, simulate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data"))
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--events", type=int, default=400_000, help="events per instrument")
    args = parser.parse_args()

    cfg = SimConfig(seed=args.seed, events_per_instrument=args.events)
    start = time.perf_counter()
    result = simulate(cfg)
    sim_seconds = time.perf_counter() - start
    labels = write_dataset(result, args.out, cfg.window_s)

    positives = labels[labels.label != "NORMAL"]
    print(f"events: {len(result.events):,} in {sim_seconds:.1f}s "
          f"({len(result.events) / sim_seconds:,.0f} events/s generated)")
    print(f"trader-windows: {len(labels):,}, manipulation windows: {len(positives):,} "
          f"({100 * len(positives) / len(labels):.2f}%)")
    print(positives.groupby(["label", "difficulty"]).size().unstack(fill_value=0))
    print(f"episodes: {len(result.episodes)}; written to {args.out}/")


if __name__ == "__main__":
    main()
