"""Run the Python and Java engines on the same events and compare every output."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

import lightgbm as lgb
import numpy as np
import pandas as pd

from sentinel.detect.rules import Rule
from sentinel.features.extractor import FEATURE_NAMES, event_columns, rows_to_frame
from sentinel.stream import java as jv
from sentinel.stream.engine import PythonEngine, StreamAlert
from sentinel.stream.export import write_engine_config, write_events

COUNT_FEATURES = ("n_new", "n_cancel", "n_modify", "n_fills", "total_new_qty", "total_fill_qty", "max_order_qty",
                  "max_live_levels_same_side", "max_cancel_burst_100ms", "n_counterparties")
KEY = ["trader_id", "instrument", "window"]
RTOL = 1e-9


@dataclass
class ParityReport:
    """Outcome of one comparison. ``mismatches`` is empty when the engines agree."""

    n_rows: int = 0
    n_alerts: int = 0
    mismatches: list[str] = field(default_factory=list)


def run_python_engine(booster: lgb.Booster, thresholds: Mapping[str, float],
                      rule_sets: Mapping[str, Sequence[Rule]], events: pd.DataFrame
                      ) -> tuple[pd.DataFrame, np.ndarray, list[StreamAlert]]:
    """Feature rows (one DataFrame), class probabilities (aligned) and alerts from the Python engine."""
    engine = PythonEngine(booster, thresholds, rule_sets)
    rows, probas, alerts = [], [], []
    outputs = (engine.process(*event) for event in zip(*event_columns(events)))
    for out in [*outputs, engine.flush()]:
        rows += out.rows
        alerts += out.alerts
        if out.proba is not None:
            probas.append(out.proba)
    return rows_to_frame(rows), np.vstack(probas), alerts


def compare_engines(events: pd.DataFrame, booster: lgb.Booster, thresholds: Mapping[str, float],
                    rule_sets: Mapping[str, Sequence[Rule]], workdir: Path, n_events: int | None = None
                    ) -> ParityReport:
    """Run both engines over ``events`` (optionally a prefix) and report every disagreement."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    events = events.iloc[:n_events] if n_events else events
    names = write_events(events, workdir / "events.bin")
    write_engine_config(workdir / "engine.properties", window_s=10, instruments=names,
                        thresholds=thresholds, rule_sets=rule_sets)
    booster.save_model(str(workdir / "lgbm.txt"))
    jv.build()
    jv.run_engine(workdir / "events.bin", workdir / "engine.properties", workdir / "lgbm.txt", workdir / "java_out")

    py_rows, py_proba, py_alerts = run_python_engine(booster, thresholds, rule_sets, events)
    jv_rows = pd.read_csv(workdir / "java_out" / "features.csv")
    jv_alerts = pd.read_csv(workdir / "java_out" / "alerts.csv")

    report = ParityReport(n_rows=len(py_rows), n_alerts=len(py_alerts))
    jv_rows = jv_rows.rename(columns={"trader": "trader_id"})
    jv_rows["instrument"] = jv_rows["instrument"].map(dict(enumerate(names)))
    py = py_rows.assign(**{f"p{k}": py_proba[:, k] for k in range(4)}).sort_values(KEY, ignore_index=True)
    jv_sorted = jv_rows.sort_values(KEY, ignore_index=True)

    if len(py) != len(jv_sorted) or not py[KEY].equals(jv_sorted[KEY]):
        report.mismatches.append(f"feature row keys differ: python {len(py)} rows, java {len(jv_sorted)} rows")
        return report
    for column in [*FEATURE_NAMES, "p0", "p1", "p2", "p3"]:
        a, b = py[column].to_numpy(), jv_sorted[column].to_numpy()
        ok = a == b if column in COUNT_FEATURES else np.isclose(a, b, rtol=RTOL, atol=1e-12)
        if not ok.all():
            bad = np.nonzero(~ok)[0][:3]
            report.mismatches.append(f"{column}: {int((~ok).sum())} rows differ, e.g. "
                                     + "; ".join(f"{py.loc[i, KEY].tolist()} python={a[i]!r} java={b[i]!r}" for i in bad))

    jv_alerts["instrument"] = jv_alerts["instrument"].map(dict(enumerate(names)))
    py_set = {(a.detector, a.kind, a.trader_id, a.instrument, a.window) for a in py_alerts}
    jv_set = {tuple(r) for r in jv_alerts[["detector", "kind", "trader", "instrument", "window"]].to_numpy().tolist()}
    if len(py_alerts) != len(jv_alerts) or py_set != jv_set:
        report.mismatches.append(
            f"alert sets differ: python {len(py_alerts)} ({len(py_set)} unique), java {len(jv_alerts)} ({len(jv_set)} unique); "
            f"only-python {sorted(py_set - jv_set)[:3]}, only-java {sorted(jv_set - py_set)[:3]}")
    return report
