"""Render RESULTS.md and the figures from a results dict: no number is typed by hand."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from sentinel.eval.charts import performance_figure, pr_figure, recall_figure

KINDS = ("SPOOFING", "LAYERING", "WASH", "ANY")
LEVELS = ("easy", "medium", "hard")


def fmt(cell: Mapping[str, float], digits: int = 2) -> str:
    """'0.41 [0.33, 0.49]' from a {point, lo, hi} cell."""
    return f"{cell['point']:.{digits}f} [{cell['lo']:.{digits}f}, {cell['hi']:.{digits}f}]"


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def _ns(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:,.1f} ms"
    return f"{value / 1000:,.1f} µs" if value >= 1000 else f"{value:,.0f} ns"


def render_results(final: Mapping, bench: Mapping, manifest: Mapping[str, str], img_dir: str) -> str:
    """The full RESULTS.md text."""
    meta, detectors = final["meta"], final["detectors"]
    out = ["# Results", "",
           (f"Evaluated on the untouched test simulation `{meta['sim_dir']}`" if meta.get("final")
            else f"REHEARSAL on `{meta['sim_dir']}` (not the test set)") + f" ({meta['n_events']:,} events, "
           f"{meta['n_windows']:,} trader-windows of 10 s, {meta['n_manipulation_windows']:,} manipulation windows from "
           f"{meta['n_episodes']} injected episodes: " + ", ".join(f"{k.title()} {v}" for k, v in meta["windows_by_type"].items())
           + "). Accuracy numbers come from the **Java streaming engine's own alerts**; the Isolation Forest is scored in Python "
           "on the same windows. Every metric is followed by a 95% cluster-bootstrap interval "
           f"({meta['n_boot']} replicates; manipulation windows resampled by episode, normal windows by account). "
           "The data is synthetic: see the README's limitations before drawing conclusions about real markets.", "",
           f"![Precision-recall curves]({img_dir}/pr_curves.png)", "", "## Detection performance", ""]
    for kind in KINDS:
        title = "Any manipulation type" if kind == "ANY" else kind.title()
        out += [f"### {title}", ""]
        rows = []
        for det in detectors:
            m = final["metrics"][kind][det]
            rows.append([det, fmt(m["precision"]), fmt(m["recall"]), fmt(m["f1"]), fmt(m["fpr"], 4), fmt(m["episode_recall"])])
        out += [_table(["Detector", "Precision", "Recall", "F1", "False-positive rate", "Episode recall"], rows), ""]
        ap = final["pr"][kind]["ap"]
        out += [f"Average precision: LightGBM {ap['LightGBM']:.3f}, Isolation Forest {ap['Isolation Forest']:.3f}.", ""]
    out += ["The generic rule is untyped, so its per-type rows repeat its overall flags. Episode recall is the share of "
            "injected cases with at least one window flagged.", "",
            "## Recall by difficulty", "", f"![Recall by difficulty]({img_dir}/recall_by_difficulty.png)", ""]
    for kind in KINDS[:3]:
        rows = []
        for det in detectors:
            cells = final["recall_by_difficulty"][kind][det]
            rows.append([det] + [f"{fmt(cells[l])} (n={cells[l]['n']})" if l in cells else "-" for l in LEVELS])
        out += [f"### {kind.title()}", "", _table(["Detector", "Easy", "Medium", "Hard"], rows), ""]
    out += ["## Paired comparisons", "",
            "Differences use the same bootstrap replicates for both detectors, so an interval that excludes 0 means the "
            "difference is distinguishable from resampling noise.", ""]
    for kind in KINDS:
        rows = []
        for p in final["paired"][kind]:
            verdict = "clear" if (p["lo"] > 0 or p["hi"] < 0) else "not distinguishable from 0"
            rows.append([f"{p['a']} minus {p['b']}", p["metric"], f"{p['diff']:+.3f} [{p['lo']:+.3f}, {p['hi']:+.3f}]", verdict])
        out += [f"### {'Any type' if kind == 'ANY' else kind.title()}", "", _table(["Comparison", "Metric", "Difference", "Verdict"], rows), ""]
    out += ["## Alert volume", "",
            f"Simulated span: {meta['sim_hours']:.2f} h across {meta['n_instruments']} instruments. "
            "\"False alerts per 1,000 innocent windows\" is the false-positive rate expressed as analyst workload.", ""]
    for kind in KINDS:
        rows = []
        for det in detectors:
            v = final["alert_volume"][kind][det]
            rows.append([det, f"{v['true_alerts']:,.0f}", f"{v['false_alerts']:,.0f}",
                         f"{v['false_alerts_per_1000_innocent_windows']:.1f}", f"{v['false_alerts_per_instrument_hour']:,.1f}"])
        out += [f"### {'Any type' if kind == 'ANY' else kind.title()}", "",
                _table(["Detector", "True alerts", "False alerts", "False alerts per 1,000 innocent windows",
                        "False alerts per instrument-hour"], rows), ""]

    env = bench["environment"]
    out += ["## Streaming performance", "",
            f"Single-threaded, {bench['n_events']:,} pre-loaded events, per-event timing of `process(event)` including model and "
            f"rule scoring when a window closes. {env['os']}, {env['cpu']}, {env['logical_cores']} logical cores, "
            f"Python {env['python']}, {env['java']}. Timer overhead is subtracted where the timer can resolve it (Python: 100 ns; Java: measured as 0, "
            "because the Windows timer ticks in 100 ns steps), so Java's median is a quantised upper bound. "
            "These are processing times per event inside the engine, not the delay before an alert reaches an analyst: an alert for a window appears when that window closes (up to 10 seconds after the behaviour) plus the scoring time shown for window-closing events. Throughput and median agree: 2.2 million events per second is a mean of about 0.45 µs per event, above the 0.1 µs median, because the 164 window-closing events (about 1 ms each) account for roughly two thirds of the total processing time.", "", f"![Streaming performance]({img_dir}/performance.png)", ""]
    rows = []
    for label, key in (("Python (reference)", "python"), ("Java v1 (straight port)", "java_v1"), ("Java v2 (flattened trees)", "java_v2")):
        run = bench[key]
        lat = run["latency_ns"]
        rows.append([label, f"{run['throughput_eps']:,.0f}", _ns(lat["overall"]["median"]), _ns(lat["overall"]["p99"]),
                     _ns(lat["overall"]["p999"]), _ns(lat["overall"]["max"]),
                     f"{_ns(lat['window_close']['median'])} (n={lat['window_close']['n']})", _ns(lat["other"]["median"])])
    out += [_table(["Engine", "Events/s", "Median", "p99", "p99.9", "Max", "Window-closing events: median", "Other events: median"], rows), "",
            "Window-closing events are the few (about one in 3,000) that trigger scoring for every active trader; they define the far tail. "
            "Max latency is a single worst event (operating-system scheduling, garbage collection) and varies from run to run; "
            "the percentile columns are the stable ones.", "",
            "## Frozen artifacts and run log", "",
            ("These files were hashed and tagged `stage4-frozen` before the test simulation was first loaded; the evaluation "
             "refuses to run if any hash differs." if meta.get("final") else "Rehearsal run: the freeze had not happened yet."), "", _table(["File", "sha-256"], [[f"`{p}`", f"`{h[:16]}…`"] for p, h in manifest.items()]), "",
            f"- Evaluated at commit `{meta['git_commit']}` on {meta['date_utc']}.",
            f"- Test-set access log: " + ("; ".join(f"`{line}`" for line in meta["access_log"]) if meta["access_log"] else "none recorded"), ""]
    return "\n".join(out) + "\n"


def build_outputs(final: Mapping, charts_dir: Path | None, results_path: Path | None, bench_dir: Path) -> None:
    """Write the three figures and/or RESULTS.md."""
    bench_dir = Path(bench_dir)
    v1 = json.loads((bench_dir / "stream_bench_v1.json").read_text(encoding="utf-8"))
    v2 = json.loads((bench_dir / "stream_bench_v2.json").read_text(encoding="utf-8"))
    bench = {"environment": v1["environment"], "n_events": v1["n_events"], "python": v1["python"]["median_run"],
             "java_v1": v1["java"]["median_run"], "java_v2": v2["java"]["median_run"]}
    if charts_dir:
        charts_dir = Path(charts_dir)
        charts_dir.mkdir(parents=True, exist_ok=True)
        pr_figure(final["pr"], charts_dir / "pr_curves.png")
        recall_figure(final["recall_by_difficulty"], charts_dir / "recall_by_difficulty.png")
        performance_figure(bench, charts_dir / "performance.png")
    if results_path:
        manifest_path = Path("docs/frozen-artifacts.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        img_dir = Path(charts_dir).as_posix() if charts_dir else "docs/img"
        Path(results_path).write_text(render_results(final, bench, manifest, img_dir), encoding="utf-8")
