"""Benchmark the Python (and, once built, Java) streaming engines.

python scripts/bench_stream.py [--engines python,java] [--n 500000] [--repeats 3] [--profile]

Uses the seed-7 event stream only (never the final test simulation).
"""
from __future__ import annotations

import argparse
import cProfile
import json
import os
import platform
import pstats
import subprocess
import sys
from pathlib import Path

import pandas as pd

from sentinel.features.extractor import event_columns
from sentinel.stream.bench import bench_python
from sentinel.stream.engine import PythonEngine, load_engine_parts


def environment() -> dict[str, str]:
    """Hardware and toolchain versions to publish next to the numbers."""
    java = "n/a"
    try:
        java = subprocess.run(["java", "-version"], capture_output=True, text=True).stderr.splitlines()[0]
    except (OSError, IndexError):
        pass
    return {"os": platform.platform(), "cpu": platform.processor(), "logical_cores": str(os.cpu_count()),
            "python": sys.version.split()[0], "java": java}


def median_run(runs: list[dict]) -> dict:
    """The run with the median throughput."""
    return sorted(runs, key=lambda r: r["throughput_eps"])[len(runs) // 2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, default=Path("data/sim_seed7/events.parquet"))
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    parser.add_argument("--n", type=int, default=500_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--engines", default="python,java")
    parser.add_argument("--out", type=Path, default=Path("artifacts/stream_bench.json"))
    parser.add_argument("--profile", action="store_true", help="cProfile one Python pass and print hotspots")
    args = parser.parse_args()

    events = pd.read_parquet(args.events).iloc[: args.n]
    booster, thresholds, rule_sets = load_engine_parts(args.model_dir)
    result: dict = {"environment": environment(), "n_events": len(events)}

    if "python" in args.engines:
        columns = event_columns(events)
        make = lambda: PythonEngine(booster, thresholds, rule_sets)  # noqa: E731
        if args.profile:
            profiler = cProfile.Profile()
            engine = make()
            profiler.enable()
            for event in zip(*columns):
                engine.process(*event)
            engine.flush()
            profiler.disable()
            pstats.Stats(profiler).sort_stats("tottime").print_stats(15)
        runs = [bench_python(make, columns, len(events)) for _ in range(args.repeats)]
        result["python"] = {"runs": runs, "median_run": median_run(runs)}

    if "java" in args.engines:
        from sentinel.stream import java as jv
        from sentinel.stream.export import write_engine_config, write_events

        work = Path("artifacts/java_bench")
        work.mkdir(parents=True, exist_ok=True)
        names = write_events(events, work / "events.bin")
        write_engine_config(work / "engine.properties", window_s=10, instruments=names,
                            thresholds=thresholds, rule_sets=rule_sets)
        booster.save_model(str(work / "lgbm.txt"))
        jv.build()
        runs = [jv.bench_engine(work / "events.bin", work / "engine.properties", work / "lgbm.txt", len(events))
                for _ in range(args.repeats)]
        result["java"] = {"runs": runs, "median_run": median_run(runs)}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    for name in ("python", "java"):
        if name in result:
            m = result[name]["median_run"]
            lat = m["latency_ns"]
            print(f"{name:7s} {m['throughput_eps']:>12,.0f} events/s | median {lat['overall']['median']:,} ns, "
                  f"p99 {lat['overall']['p99']:,} ns, p99.9 {lat['overall']['p999']:,} ns, max {lat['overall']['max']:,} ns")
            print(f"        window-close events: n={lat['window_close']['n']}, median {lat['window_close']['median']:,} ns, "
                  f"max {lat['window_close']['max']:,} ns | other: median {lat['other']['median']:,} ns, "
                  f"p99 {lat['other']['p99']:,} ns")


if __name__ == "__main__":
    main()
