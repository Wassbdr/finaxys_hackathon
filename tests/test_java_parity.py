"""Java engine tests (skipped when no JDK is on PATH)."""
from __future__ import annotations

import pytest

from sentinel.detect.dataset import to_xy
from sentinel.detect.model import predict_proba, train_lgbm
from sentinel.features.extractor import FEATURE_NAMES
from sentinel.stream import java as jv

pytestmark = pytest.mark.skipif(not jv.available(), reason="JDK not found")


@pytest.fixture(scope="module")
def built():
    return jv.build()


def _write_model_fixture(synthetic_windows, tmp_path):
    train, val = synthetic_windows.iloc[:1600], synthetic_windows.iloc[1600:]
    booster = train_lgbm(train, val, seed=1)
    model = tmp_path / "lgbm.txt"
    booster.save_model(str(model))
    X = to_xy(synthetic_windows.iloc[:200])[0]
    proba = predict_proba(booster, X)
    fixture = tmp_path / "model_cases.csv"
    fixture.write_text("\n".join(",".join(repr(float(v)) for v in [*x, *p]) for x, p in zip(X, proba)) + "\n",
                       encoding="utf-8")
    return booster, model, fixture


def test_java_tree_evaluator_reproduces_python_probabilities(built, synthetic_windows, tmp_path) -> None:
    _, model, fixture = _write_model_fixture(synthetic_windows, tmp_path)
    assert "200 cases, 0 mismatches" in jv.selftest(model, fixture)
    assert len(FEATURE_NAMES) == 22


def test_java_engine_matches_python_engine_on_a_real_simulation(built, synthetic_windows, tmp_path) -> None:
    from sentinel.detect.rules import GENERIC_RULE, TEXTBOOK_RULES
    from sentinel.sim.runner import SimConfig, simulate
    from sentinel.stream.parity import compare_engines

    booster, _, _ = _write_model_fixture(synthetic_windows, tmp_path)
    thresholds = {"SPOOFING": 0.3, "LAYERING": 0.3, "WASH": 0.3}
    rule_sets = {"textbook": list(TEXTBOOK_RULES.values()), "generic": [GENERIC_RULE]}
    res = simulate(SimConfig(instruments=("A", "B"), events_per_instrument=20_000, episodes_per_instrument=15, seed=6))
    report = compare_engines(res.events, booster, thresholds, rule_sets, tmp_path / "parity")
    assert report.mismatches == [], "\n".join(report.mismatches)
    assert report.n_rows > 100 and report.n_alerts > 0
