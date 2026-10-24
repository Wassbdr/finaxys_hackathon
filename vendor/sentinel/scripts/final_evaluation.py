"""Evaluate all detectors on a simulation directory (rehearsal or the final test set).

python scripts/final_evaluation.py --sim data/rehearsal_seed200
python scripts/final_evaluation.py --sim data/test_seed100 --final --charts-dir docs/img --results RESULTS.md

Test directories run only with --final and only if the frozen-artifact manifest still matches.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sentinel.eval.final import run_final_evaluation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sim", type=Path, required=True)
    parser.add_argument("--final", action="store_true")
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    parser.add_argument("--n-boot", type=int, default=2000)
    parser.add_argument("--out", type=Path, default=None, help="results JSON (default artifacts/final_eval_<sim>.json)")
    parser.add_argument("--charts-dir", type=Path, default=None)
    parser.add_argument("--results", type=Path, default=None, help="write the generated RESULTS.md here")
    parser.add_argument("--bench-dir", type=Path, default=Path("docs/bench"))
    args = parser.parse_args()

    result = run_final_evaluation(args.sim, args.model_dir, final=args.final, n_boot=args.n_boot)
    out = args.out or Path("artifacts") / f"final_eval_{args.sim.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    meta = result["meta"]
    print(f"{meta['sim_dir']}: {meta['n_events']:,} events, {meta['n_windows']:,} windows, "
          f"{meta['n_manipulation_windows']:,} manipulation windows, {meta['n_episodes']} episodes")
    for kind in ("SPOOFING", "LAYERING", "WASH", "ANY"):
        for det in result["detectors"]:
            f1 = result["metrics"][kind][det]["f1"]
            print(f"  {kind:9s} {det:18s} F1 {f1['point']:.3f} [{f1['lo']:.3f}, {f1['hi']:.3f}]")
    print(f"wrote {out}")

    if args.charts_dir or args.results:
        from sentinel.eval.results_md import build_outputs

        build_outputs(result, args.charts_dir, args.results, args.bench_dir)


if __name__ == "__main__":
    main()
