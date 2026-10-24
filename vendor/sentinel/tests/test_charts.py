"""Smoke tests for the figures."""
from __future__ import annotations

from sentinel.eval.charts import performance_figure, pr_figure, recall_figure
from sentinel.eval.final import evaluate
from tests.test_final import synthetic_eval_inputs

PNG = b"\x89PNG\r\n\x1a\n"


def _result():
    df, flags, scores = synthetic_eval_inputs()
    return evaluate(df, flags, scores, n_instruments=2, sim_hours=1.0, n_boot=60, seed=1)


def _bench():
    lat = lambda m, p, q, x: {"overall": {"median": m, "p99": p, "p999": q, "max": x}}  # noqa: E731
    return {"python": {"throughput_eps": 179_000.0, "latency_ns": lat(4_700, 14_200, 59_700, 3_000_000)},
            "java_v2": {"throughput_eps": 2_200_000.0, "latency_ns": lat(100, 600, 4_300, 2_000_000)}}


def test_figures_are_written_as_pngs(tmp_path) -> None:
    result = _result()
    pr_figure(result["pr"], tmp_path / "pr.png")
    recall_figure(result["recall_by_difficulty"], tmp_path / "recall.png")
    performance_figure(_bench(), tmp_path / "perf.png")
    for name in ("pr.png", "recall.png", "perf.png"):
        data = (tmp_path / name).read_bytes()
        assert data.startswith(PNG) and len(data) > 15_000, name
