"""The streaming Python engine must agree with the batch path it is built from."""
from __future__ import annotations

import numpy as np
import pandas as pd

from sentinel.detect.dataset import CLASSES, to_xy
from sentinel.detect.model import predict_proba, predict_types, train_lgbm
from sentinel.detect.rules import GENERIC_RULE, TEXTBOOK_RULES
from sentinel.features.extractor import FEATURE_NAMES, event_columns, extract_features, rows_to_frame
from sentinel.sim.runner import SimConfig, simulate
from sentinel.stream.engine import PythonEngine, load_engine_parts, rule_flags

THRESHOLDS = {"SPOOFING": 0.4, "LAYERING": 0.4, "WASH": 0.4}


def _run(engine, events):
    rows, probas, alerts = [], [], []
    for event in zip(*event_columns(events)):
        out = engine.process(*event)
        rows += out.rows
        alerts += out.alerts
        if out.proba is not None:
            probas.append(out.proba)
    out = engine.flush()
    rows += out.rows
    alerts += out.alerts
    if out.proba is not None:
        probas.append(out.proba)
    return rows, np.vstack(probas), alerts


def test_engine_output_equals_the_batch_path(synthetic_windows) -> None:
    train, val = synthetic_windows.iloc[:1600], synthetic_windows.iloc[1600:]
    booster = train_lgbm(train, val, seed=1)
    rule_sets = {"textbook": list(TEXTBOOK_RULES.values()), "generic": [GENERIC_RULE]}
    res = simulate(SimConfig(instruments=("A", "B"), events_per_instrument=15_000, episodes_per_instrument=12, seed=4))

    rows, proba, alerts = _run(PythonEngine(booster, THRESHOLDS, rule_sets), res.events)

    feats = extract_features(res.events)
    pd.testing.assert_frame_equal(rows_to_frame(rows), feats)  # same rows, same order, same values
    X = feats[list(FEATURE_NAMES)].to_numpy()
    assert np.allclose(proba, predict_proba(booster, X), atol=1e-12)

    key = ["trader_id", "instrument", "window"]
    expected = set()
    types = predict_types(predict_proba(booster, X), THRESHOLDS)
    for i in np.nonzero(types)[0]:
        expected.add(("model", CLASSES[types[i]], *feats.iloc[i][key].tolist()))
    for name, rules in rule_sets.items():
        for rule in rules:
            for i in np.nonzero(rule.evaluate(feats))[0]:
                expected.add((name, rule.kind, *feats.iloc[i][key].tolist()))
    got = {(a.detector, a.kind, a.trader_id, a.instrument, a.window) for a in alerts}
    assert got == expected and len(alerts) == len(expected)
    assert len(expected) > 0


def test_rule_flags_match_dataframe_evaluation() -> None:
    rng = np.random.default_rng(0)
    X = rng.uniform(0, 10, size=(200, len(FEATURE_NAMES)))
    df = pd.DataFrame(X, columns=list(FEATURE_NAMES))
    for rule in [*TEXTBOOK_RULES.values(), GENERIC_RULE]:
        assert np.array_equal(rule_flags(rule, X), rule.evaluate(df))


def test_nothing_is_scored_until_a_window_closes(synthetic_windows) -> None:
    booster = train_lgbm(synthetic_windows.iloc[:1600], synthetic_windows.iloc[1600:], seed=1)
    engine = PythonEngine(booster, THRESHOLDS, {"generic": [GENERIC_RULE]})
    zeros = (0, 0, 0, 0, 0, 0)
    first = engine.process(1_000_000_000, "A", 0, 1, 7, 0, 1, 100, 10, *zeros)
    assert first.rows == [] and first.proba is None and first.alerts == []
    closing = engine.process(11_000_000_000, "A", 0, 2, 7, 0, 1, 100, 10, *zeros)
    assert len(closing.rows) == 1 and closing.proba.shape == (1, 4)


def test_load_engine_parts_reads_the_saved_model_directory(synthetic_windows, tmp_path) -> None:
    from sentinel.detect.model import save_model
    from sentinel.detect.rules import save_rules

    booster = train_lgbm(synthetic_windows.iloc[:1600], synthetic_windows.iloc[1600:], seed=1)
    save_model(booster, THRESHOLDS, tmp_path)
    save_rules(tmp_path / "tuned_rules.json", {"WASH": TEXTBOOK_RULES["WASH"]})
    loaded, thresholds, rule_sets = load_engine_parts(tmp_path)
    assert thresholds == THRESHOLDS
    assert list(rule_sets) == ["textbook", "tuned", "generic"] and len(rule_sets["textbook"]) == 3
    assert rule_sets["tuned"] == [TEXTBOOK_RULES["WASH"]] and rule_sets["generic"] == [GENERIC_RULE]
    X = to_xy(synthetic_windows.iloc[1600:])[0]
    assert np.allclose(predict_proba(loaded, X), predict_proba(booster, X))
