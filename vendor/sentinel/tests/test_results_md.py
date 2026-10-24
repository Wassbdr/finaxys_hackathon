"""Tests for the RESULTS.md renderer."""
from __future__ import annotations

from sentinel.eval.final import evaluate
from sentinel.eval.results_md import fmt, render_results
from tests.test_final import synthetic_eval_inputs


def _final():
    df, flags, scores = synthetic_eval_inputs()
    result = evaluate(df, flags, scores, n_instruments=2, sim_hours=1.0, n_boot=60, seed=1)
    result["meta"] = {"sim_dir": "test_seed100", "n_events": 1_600_000, "n_windows": 24_000,
                      "n_manipulation_windows": 1_000, "n_episodes": 390, "windows_by_type": {"SPOOFING": 300, "LAYERING": 300, "WASH": 400},
                      "n_instruments": 4, "sim_hours": 0.3, "n_boot": 60, "seed": 1, "git_commit": "abc123",
                      "date_utc": "2026-09-24T00:00:00+00:00", "final": True, "n_java_alerts": 5, "n_rows": 24_000,
                      "access_log": ["2026-09-24T00:00:00+00:00\tdata\test_seed100"]}
    return result


def _bench():
    run = lambda eps, m, p, q, x, w: {"throughput_eps": eps, "n_events": 500_000, "latency_ns": {  # noqa: E731
        "overall": {"median": m, "p99": p, "p999": q, "max": x, "n": 500_000},
        "window_close": {"median": w, "p99": w, "p999": w, "max": w, "n": 164},
        "other": {"median": m, "p99": p, "p999": q, "max": x, "n": 499_836}}}
    return {"environment": {"os": "Windows", "cpu": "AMD", "logical_cores": "16", "python": "3.12", "java": "openjdk 25"},
            "n_events": 500_000, "python": run(179_000.0, 4_700, 14_200, 59_700, 3_000_000, 1_300_000),
            "java_v1": run(1_975_000.0, 100, 700, 5_300, 3_700_000, 2_000_000),
            "java_v2": run(2_200_000.0, 100, 600, 4_300, 2_000_000, 950_000)}


def test_fmt_shows_point_and_interval() -> None:
    assert fmt({"point": 0.4123, "lo": 0.3, "hi": 0.5}) == "0.41 [0.30, 0.50]"


def test_render_results_contains_every_section_and_generated_numbers() -> None:
    manifest = {"models/lgbm.txt": "a" * 64}
    md = render_results(_final(), _bench(), manifest, "docs/img")
    for heading in ("# Results", "## Detection performance", "## Recall by difficulty", "## Paired comparisons",
                    "## Alert volume", "## Streaming performance", "## Frozen artifacts and run log"):
        assert heading in md, heading
    assert "| LightGBM |" in md and "Isolation Forest" in md and "test_seed100" in md
    assert "aaaaaaaaaaaaaaaa" in md and "abc123" in md and "docs/img/pr_curves.png" in md
    assert "179,000" in md and "2,200,000" in md
