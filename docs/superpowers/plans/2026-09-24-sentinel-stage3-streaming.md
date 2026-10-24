# Sentinel Stage 3: Streaming Engine (Python reference and Java port) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Commit rule:** every commit message ends with the line `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` (use a heredoc).

**Goal:** A single-threaded streaming detection engine in Python (reference) and Java (port) that consumes events one at a time, computes the Stage 2 features, scores rules and the LightGBM model when a window closes, and emits alerts; prove the Java port matches the Python reference; measure throughput and per-event latency for both.

**Architecture:** `PythonEngine` wraps the existing `WindowFeatureExtractor` and scores each closed window with LightGBM and three rule sets. The Java engine is a line-for-line port (extractor, rules, LightGBM text-model evaluator) with no dependencies. Python exports events (binary) and the engine config (properties); a parity module runs both engines on the same events and compares feature rows, probabilities and alert sets. A benchmark harness times `process(event)` per event in both languages.

**Tech Stack:** Python 3.12, numpy, pandas, lightgbm, pytest; Java (JDK 25 is installed; `javac`/`java` on PATH; no Maven, no JUnit).

Spec: `docs/superpowers/specs/2026-09-24-sentinel-streaming-design.md`

---

## File Structure

```
java/.gitignore                          (out/)
java/src/sentinel/LgbmModel.java         LightGBM text-model parser and evaluator
java/src/sentinel/SelfTest.java          checks the evaluator against a Python-made fixture
java/src/sentinel/Config.java            engine.properties and rules
java/src/sentinel/FeatureExtractor.java  port of the Python extractor
java/src/sentinel/Engine.java            extractor + model + rules
java/src/sentinel/EventReader.java       memory-mapped events.bin
java/src/sentinel/Main.java              `run` and `bench` modes
src/sentinel/detect/rules.py             (modify: save_rules / load_rules)
src/sentinel/features/extractor.py       (modify: public event_columns)
src/sentinel/stream/__init__.py
src/sentinel/stream/export.py            events.bin, engine.properties
src/sentinel/stream/engine.py            PythonEngine, load_engine_parts
src/sentinel/stream/bench.py             percentiles, bench_python
src/sentinel/stream/java.py              build/run/bench the Java engine
src/sentinel/stream/parity.py            compare Python and Java engines
scripts/train_detectors.py               (modify: persist tuned rules)
scripts/bench_stream.py
scripts/parity_check.py
tests/test_rules.py                      (modify)
tests/test_stream_export.py
tests/test_stream_engine.py
tests/test_stream_bench.py
tests/test_java_parity.py
```

---

### Task 0: Persist tuned rules

The exporter needs the tuned rules, but Stage 2 only printed them. Save them as JSON and re-run the (deterministic) training pipeline.

**Files:** Modify `src/sentinel/detect/rules.py`, `scripts/train_detectors.py`, `tests/test_rules.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_rules.py`)

```python
def test_rules_round_trip_through_json(tmp_path) -> None:
    from sentinel.detect.rules import load_rules, save_rules

    rules = {"WASH": Rule("WASH", (Condition("n_fills", ">=", 6.7500000001234),
                                   Condition("net_over_gross", "<=", 0.345))),
             "ANY": Rule("ANY", (Condition("cancel_rate", ">=", 0.9),), mode="any")}
    save_rules(tmp_path / "rules.json", rules)
    assert load_rules(tmp_path / "rules.json") == rules
```
Run: `python -m pytest tests/test_rules.py -k json -v`. Expected: FAIL with `ImportError: cannot import name 'save_rules'`.

- [ ] **Step 2: Implement** (append to `src/sentinel/detect/rules.py`; add `import json` and `from pathlib import Path` at the top with the other imports)

```python
def save_rules(path: Path, rules: Mapping[str, Rule]) -> None:
    """Write rules to JSON, keyed by name. Thresholds keep full float precision."""
    data = {name: {"kind": r.kind, "mode": r.mode,
                   "conditions": [[c.feature, c.op, c.threshold] for c in r.conditions]}
            for name, r in rules.items()}
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_rules(path: Path) -> dict[str, Rule]:
    """Inverse of save_rules."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {name: Rule(d["kind"], tuple(Condition(f, op, float(t)) for f, op, t in d["conditions"]), d["mode"])
            for name, d in data.items()}
```

- [ ] **Step 3: Persist in the pipeline.** In `scripts/train_detectors.py` import `save_rules` alongside the other rules imports, and right after `save_model(booster, thresholds, Path("models"))` add:
```python
    save_rules(Path("models") / "tuned_rules.json", tuned)
```

- [ ] **Step 4: Run tests, then regenerate the models**

Run: `python -m pytest -q` (all pass), then `python scripts/train_detectors.py` (it is deterministic, so metrics equal the last report: LightGBM SPOOFING F1 0.407, LAYERING 0.619, WASH 0.318; if they differ, STOP and report). Confirm `models/tuned_rules.json`, `models/lgbm.txt`, `models/thresholds.json` exist. Do NOT touch `data/test_seed100`.

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/detect/rules.py scripts/train_detectors.py tests/test_rules.py
git commit -m "$(cat <<'EOF'
feat(detect): persist tuned rules as JSON for the streaming engines

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1: Export formats (`events.bin`, `engine.properties`)

**Concept to explain:** the Java engine must see exactly the same inputs as the Python one. Events go in a fixed-width binary file (15 little-endian int64s per event, so Java can memory-map it and never parse text), and the model, thresholds and rules go in a text properties file so nothing is hard-coded twice. Floats are written with `repr`, which round-trips exactly.

**Files:**
- Modify: `src/sentinel/features/extractor.py`
- Create: `src/sentinel/stream/__init__.py`, `src/sentinel/stream/export.py`
- Test: `tests/test_stream_export.py`

- [ ] **Step 1: Refactor `extract_features` to use a public `event_columns`.** In `src/sentinel/features/extractor.py`, replace the body of `extract_features` from the `missing = ...` line onward with a call to a new function, so the column preparation is shared:

```python
def event_columns(events: pd.DataFrame) -> list[list]:
    """The 15 input columns as Python lists in ``process`` argument order.

    ``instrument`` is a string and ``event_type`` an int code (NEW=0, CANCEL=1, MODIFY=2, EXEC=3).
    """
    missing = [c for c in _INPUT_COLUMNS if c not in events.columns]
    if missing:
        raise ValueError(f"event log is missing columns: {missing}")
    columns = []
    for name in _INPUT_COLUMNS:
        if name == "event_type":
            columns.append(events[name].astype(str).map(_EVENT_CODES).to_numpy().tolist())
        elif name == "instrument":
            columns.append(events[name].astype(str).to_numpy().tolist())
        else:
            columns.append(events[name].to_numpy().tolist())
    return columns


def extract_features(events: pd.DataFrame, window_s: int = 10) -> pd.DataFrame:
    """Replay an event log (time-ordered) through the streaming extractor."""
    extractor = WindowFeatureExtractor(window_s)
    rows: list[FeatureRow] = []
    for event in zip(*event_columns(events)):
        rows += extractor.process(*event)
    rows += extractor.flush()
    return rows_to_frame(rows)
```
Run `python -m pytest -q`: all existing tests must still pass.

- [ ] **Step 2: Write `src/sentinel/stream/__init__.py`**

```python
"""Streaming engines (Python reference and Java port support) and benchmarking."""
```

- [ ] **Step 3: Write the failing tests** (`tests/test_stream_export.py`)

```python
"""Tests for the binary event format and the engine config file."""
from __future__ import annotations

import struct

import numpy as np

from sentinel.detect.rules import GENERIC_RULE, TEXTBOOK_RULES, Condition, Rule
from sentinel.features.extractor import FEATURE_NAMES
from sentinel.sim.runner import SimConfig, simulate
from sentinel.stream.export import EVENT_MAGIC, read_events, write_engine_config, write_events


def _props(path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            out[key] = value
    return out


def test_events_round_trip_through_the_binary_format(tmp_path) -> None:
    res = simulate(SimConfig(instruments=("B", "A"), events_per_instrument=3_000, episodes_per_instrument=3, seed=1))
    names = write_events(res.events, tmp_path / "events.bin")
    assert names == ["A", "B"]  # instrument index follows sorted order, not first appearance
    arr = read_events(tmp_path / "events.bin")
    ev = res.events
    assert arr.shape == (len(ev), 15)
    codes = ev.event_type.astype(str).map({"NEW": 0, "CANCEL": 1, "MODIFY": 2, "EXEC": 3}).to_numpy()
    expected = [ev.ts, ev.instrument.astype(str).map({"A": 0, "B": 1}), codes, ev.order_id, ev.trader_id,
                ev.counterparty_id, ev.side, ev.price, ev.qty, ev.best_bid, ev.best_ask, ev.bid_qty1,
                ev.ask_qty1, ev.bid_depth5, ev.ask_depth5]
    for column, values in enumerate(expected):
        assert np.array_equal(arr[:, column], np.asarray(values)), column


def test_file_header_is_magic_then_little_endian_count(tmp_path) -> None:
    res = simulate(SimConfig(instruments=("A",), events_per_instrument=1_000, episodes_per_instrument=2, seed=2))
    write_events(res.events, tmp_path / "events.bin")
    raw = (tmp_path / "events.bin").read_bytes()
    assert raw[:8] == EVENT_MAGIC and struct.unpack("<q", raw[8:16])[0] == len(res.events)
    assert len(raw) == 16 + len(res.events) * 15 * 8


def test_config_lists_features_thresholds_and_rules_with_exact_floats(tmp_path) -> None:
    tuned = {"WASH": Rule("WASH", (Condition("n_fills", ">=", 6.7500000001234),))}
    write_engine_config(
        tmp_path / "engine.properties", window_s=10, instruments=["A", "B"],
        thresholds={"SPOOFING": 0.85, "LAYERING": 0.65, "WASH": 0.83},
        rule_sets={"textbook": list(TEXTBOOK_RULES.values()), "tuned": list(tuned.values()), "generic": [GENERIC_RULE]})
    props = _props(tmp_path / "engine.properties")
    assert props["window_s"] == "10" and props["instruments"] == "A,B"
    assert props["features"].split(",") == list(FEATURE_NAMES)
    assert props["threshold.WASH"] == "0.83" and props["threshold.SPOOFING"] == "0.85"
    assert props["rule.count"] == "5"
    assert (props["rule.0.detector"], props["rule.0.kind"], props["rule.0.mode"]) == ("textbook", "SPOOFING", "all")
    assert "cancel_rate>=0.5" in props["rule.0.conditions"].split(";")
    assert (props["rule.3.detector"], props["rule.3.kind"]) == ("tuned", "WASH")
    assert float(props["rule.3.conditions"].split(">=")[1]) == 6.7500000001234
    assert (props["rule.4.detector"], props["rule.4.mode"]) == ("generic", "any")
```
Run: `python -m pytest tests/test_stream_export.py -v`. Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.stream.export'`.

- [ ] **Step 4: Write `src/sentinel/stream/export.py`**

```python
"""Files the Java engine reads: a binary event log and a properties config."""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from sentinel.detect.dataset import CLASSES
from sentinel.detect.rules import Rule
from sentinel.features.extractor import FEATURE_NAMES
from sentinel.sim.types import CANCEL, EXEC, MODIFY, NEW

EVENT_MAGIC = b"SNTLEV01"
N_FIELDS = 15  # ts, instrument idx, event type, order id, trader id, counterparty id, side, price, qty, 6 snapshot fields
_EVENT_CODES = {"NEW": NEW, "CANCEL": CANCEL, "MODIFY": MODIFY, "EXEC": EXEC}
_SNAPSHOT = ("best_bid", "best_ask", "bid_qty1", "ask_qty1", "bid_depth5", "ask_depth5")


def events_to_array(events: pd.DataFrame, instruments: Sequence[str]) -> np.ndarray:
    """Event log as an (n, 15) little-endian int64 array in the file's field order."""
    index = {name: i for i, name in enumerate(instruments)}
    arr = np.empty((len(events), N_FIELDS), dtype="<i8")
    arr[:, 0] = events["ts"].to_numpy()
    arr[:, 1] = events["instrument"].astype(str).map(index).to_numpy()
    arr[:, 2] = events["event_type"].astype(str).map(_EVENT_CODES).to_numpy()
    for column, name in enumerate(("order_id", "trader_id", "counterparty_id", "side", "price", "qty", *_SNAPSHOT), 3):
        arr[:, column] = events[name].to_numpy()
    return arr


def write_events(events: pd.DataFrame, path: Path) -> list[str]:
    """Write events.bin (magic, int64 count, records). Returns the instrument names in index order."""
    instruments = sorted(events["instrument"].astype(str).unique())
    arr = events_to_array(events, instruments)
    with Path(path).open("wb") as f:
        f.write(EVENT_MAGIC)
        f.write(struct.pack("<q", len(arr)))
        arr.tofile(f)
    return instruments


def read_events(path: Path) -> np.ndarray:
    """Read events.bin back as an (n, 15) int64 array (used by tests)."""
    raw = Path(path).read_bytes()
    if raw[:8] != EVENT_MAGIC:
        raise ValueError("not an events.bin file")
    (count,) = struct.unpack("<q", raw[8:16])
    return np.frombuffer(raw, dtype="<i8", offset=16, count=count * N_FIELDS).reshape(count, N_FIELDS)


def write_engine_config(path: Path, *, window_s: int, instruments: Sequence[str],
                        thresholds: Mapping[str, float], rule_sets: Mapping[str, Sequence[Rule]]) -> None:
    """Write engine.properties: window length, feature order, model thresholds and every rule.

    Floats use ``repr`` so the Java side parses back the identical double.
    """
    lines = [f"window_s={window_s}", f"instruments={','.join(instruments)}", f"features={','.join(FEATURE_NAMES)}"]
    lines += [f"threshold.{kind}={float(thresholds[kind])!r}" for kind in CLASSES[1:]]
    rules = [(detector, rule) for detector, group in rule_sets.items() for rule in group]
    lines.append(f"rule.count={len(rules)}")
    for i, (detector, rule) in enumerate(rules):
        conditions = ";".join(f"{c.feature}{c.op}{float(c.threshold)!r}" for c in rule.conditions)
        lines += [f"rule.{i}.detector={detector}", f"rule.{i}.kind={rule.kind}",
                  f"rule.{i}.mode={rule.mode}", f"rule.{i}.conditions={conditions}"]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
```

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_stream_export.py -v` then `python -m pytest -q`. Expected: all pass.
```bash
git add src/sentinel/features/extractor.py src/sentinel/stream tests/test_stream_export.py
git commit -m "$(cat <<'EOF'
feat(stream): binary event export and engine.properties config

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Python streaming engine

**Concept to explain:** the engine is the Stage 2 extractor plus scoring. Nothing is scored until a window closes, and then all rows of that window are scored together (one LightGBM call per closed window, not per event).

**Files:** Create `src/sentinel/stream/engine.py`; Test `tests/test_stream_engine.py`

- [ ] **Step 1: Write the failing tests** (`tests/test_stream_engine.py`)

```python
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
```
Run: `python -m pytest tests/test_stream_engine.py -v`. Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.stream.engine'`.

- [ ] **Step 2: Write `src/sentinel/stream/engine.py`**

```python
"""Python reference streaming engine: extractor + LightGBM + rules, one event at a time."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping, NamedTuple, Sequence

import lightgbm as lgb
import numpy as np

from sentinel.detect.dataset import CLASSES
from sentinel.detect.model import load_model, predict_proba, predict_types
from sentinel.detect.rules import GENERIC_RULE, TEXTBOOK_RULES, Rule, load_rules
from sentinel.features.extractor import FEATURE_NAMES, FeatureRow, WindowFeatureExtractor

_COL = {name: i for i, name in enumerate(FEATURE_NAMES)}


class StreamAlert(NamedTuple):
    """A flagged trader-window from one detector. Explanations are added later, off the hot path."""

    detector: str  # "model", or the rule-set name: "textbook", "tuned", "generic"
    kind: str
    trader_id: int
    instrument: str
    window: int
    score: float


class EngineOutput(NamedTuple):
    """What one ``process`` call produced (all empty unless the event closed a window)."""

    rows: list[FeatureRow]
    proba: np.ndarray | None  # (n_rows, 4), rows in the same order as ``rows``
    alerts: list[StreamAlert]


_NOTHING = EngineOutput([], None, [])


def rule_flags(rule: Rule, X: np.ndarray) -> np.ndarray:
    """Evaluate a rule on a feature matrix (columns in FEATURE_NAMES order)."""
    masks = [c.holds(X[:, _COL[c.feature]]) for c in rule.conditions]
    return np.logical_and.reduce(masks) if rule.mode == "all" else np.logical_or.reduce(masks)


class PythonEngine:
    """Single-threaded engine. Feed events with ``process``; call ``flush`` at end of stream."""

    def __init__(self, booster: lgb.Booster, thresholds: Mapping[str, float],
                 rule_sets: Mapping[str, Sequence[Rule]], window_s: int = 10) -> None:
        self._extractor = WindowFeatureExtractor(window_s)
        self._booster = booster
        self._thresholds = dict(thresholds)
        self._rule_sets = {name: list(rules) for name, rules in rule_sets.items()}

    def process(self, *event) -> EngineOutput:
        """Process one event (arguments as WindowFeatureExtractor.process)."""
        rows = self._extractor.process(*event)
        return self._score(rows) if rows else _NOTHING

    def flush(self) -> EngineOutput:
        """Close and score every open window."""
        rows = self._extractor.flush()
        return self._score(rows) if rows else _NOTHING

    def _score(self, rows: list[FeatureRow]) -> EngineOutput:
        X = np.array([r.values for r in rows], dtype=np.float64)
        proba = predict_proba(self._booster, X)
        types = predict_types(proba, self._thresholds)
        alerts = [StreamAlert("model", CLASSES[k], r.trader_id, r.instrument, r.window, float(proba[i, k]))
                  for i, (r, k) in enumerate(zip(rows, types)) if k]
        for name, rules in self._rule_sets.items():
            for rule in rules:
                flags = rule_flags(rule, X)
                alerts += [StreamAlert(name, rule.kind, r.trader_id, r.instrument, r.window, 1.0)
                           for r, flagged in zip(rows, flags) if flagged]
        return EngineOutput(rows, proba, alerts)


def load_engine_parts(model_dir: Path = Path("models")) -> tuple[lgb.Booster, dict[str, float], dict[str, list[Rule]]]:
    """Booster, per-type thresholds and the three rule sets (textbook, tuned, generic) from a model directory."""
    model_dir = Path(model_dir)
    booster, thresholds = load_model(model_dir)
    tuned = load_rules(model_dir / "tuned_rules.json")
    rule_sets = {"textbook": list(TEXTBOOK_RULES.values()), "tuned": list(tuned.values()), "generic": [GENERIC_RULE]}
    return booster, thresholds, rule_sets
```

- [ ] **Step 3: Run tests and commit**

Run: `python -m pytest tests/test_stream_engine.py -v` then `python -m pytest -q`. Expected: all pass. If the alert-set test finds zero alerts (`len(expected) > 0` fails), lower `THRESHOLDS` in the test to 0.2 rather than weakening the assertion, and say so.
```bash
git add src/sentinel/stream/engine.py tests/test_stream_engine.py
git commit -m "$(cat <<'EOF'
feat(stream): Python reference streaming engine

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Benchmark harness and the Python benchmark

**Concept to explain:** latency percentiles. p99 means 99% of events were processed at least that fast. Because a window closes only about once per 3,000 events, ordinary events barely feel the scoring cost, but those closing events are slow and define the far tail (p99.9, max). So we report the two groups separately. We use the *nearest-rank* percentile (the sorted value at position ceil(p*n)) in both languages so the numbers are comparable.

**Files:** Create `src/sentinel/stream/bench.py`, `scripts/bench_stream.py`; Test `tests/test_stream_bench.py`

- [ ] **Step 1: Write the failing tests** (`tests/test_stream_bench.py`)

```python
"""Tests for latency statistics and the benchmark loop."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from sentinel.stream.bench import bench_python, latency_stats, nearest_rank, summarize


def test_nearest_rank_percentiles() -> None:
    values = np.arange(1, 101)  # 1..100
    assert nearest_rank(values, 0.5) == 50 and nearest_rank(values, 0.99) == 99
    assert nearest_rank(values, 0.999) == 100 and nearest_rank(values, 1.0) == 100
    assert nearest_rank(np.array([7]), 0.99) == 7


def test_summarize_and_latency_stats_split_and_subtract_overhead() -> None:
    lat = np.array([100, 110, 120, 130, 5_000], dtype=np.int64)
    closes = np.array([False, False, False, False, True])
    stats = latency_stats(lat, closes, overhead=10)
    assert stats["overall"]["max"] == 4_990 and stats["overall"]["median"] == 110
    assert stats["window_close"] == {"n": 1, "median": 4_990, "p99": 4_990, "p999": 4_990, "max": 4_990}
    assert stats["other"]["n"] == 4 and stats["other"]["max"] == 120
    assert summarize(np.array([], dtype=np.int64))["n"] == 0


class _CountingEngine:
    def __init__(self) -> None:
        self.calls = 0

    def process(self, *event):
        self.calls += 1
        return SimpleNamespace(rows=[1] if self.calls % 50 == 0 else [])

    def flush(self):
        return SimpleNamespace(rows=[])


def test_bench_python_times_every_event_and_flags_window_closes() -> None:
    n = 500
    columns = [list(range(n)) for _ in range(15)]
    result = bench_python(_CountingEngine, columns, n)
    assert result["engine"] == "python" and result["n_events"] == n and result["throughput_eps"] > 0
    assert result["latency_ns"]["overall"]["n"] == n
    assert result["latency_ns"]["window_close"]["n"] == n // 50
    assert result["timer_overhead_ns"] >= 0
```
Run: `python -m pytest tests/test_stream_bench.py -v`. Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.stream.bench'`.

- [ ] **Step 2: Write `src/sentinel/stream/bench.py`**

```python
"""Latency and throughput measurement for a streaming engine."""
from __future__ import annotations

import math
import time
from typing import Callable, Sequence

import numpy as np


def nearest_rank(sorted_values: np.ndarray, p: float) -> int:
    """Nearest-rank percentile: the value at 1-based position ceil(p * n) of the sorted data."""
    index = max(0, math.ceil(p * len(sorted_values)) - 1)
    return int(sorted_values[index])


def summarize(values: np.ndarray) -> dict[str, int]:
    """n, median, p99, p99.9 and max (nanoseconds) of a set of latencies."""
    if len(values) == 0:
        return {"n": 0, "median": 0, "p99": 0, "p999": 0, "max": 0}
    s = np.sort(values)
    return {"n": len(s), "median": nearest_rank(s, 0.5), "p99": nearest_rank(s, 0.99),
            "p999": nearest_rank(s, 0.999), "max": int(s[-1])}


def latency_stats(lat: np.ndarray, closes: np.ndarray, overhead: int) -> dict[str, dict[str, int]]:
    """Timer-overhead-corrected latency summary: overall, events that closed a window, and the rest."""
    adjusted = np.maximum(lat - overhead, 0)
    return {"overall": summarize(adjusted), "window_close": summarize(adjusted[closes]),
            "other": summarize(adjusted[~closes])}


def timer_overhead(timer: Callable[[], int] = time.perf_counter_ns, samples: int = 100_000) -> int:
    """Median cost of an empty timed region (two timer calls), in ns."""
    diffs = np.empty(samples, dtype=np.int64)
    for i in range(samples):
        a = timer()
        b = timer()
        diffs[i] = b - a
    return int(np.median(diffs))


def bench_python(make_engine: Callable[[], object], columns: Sequence[Sequence], n: int,
                 timer: Callable[[], int] = time.perf_counter_ns) -> dict:
    """Benchmark an engine in this process.

    Pass 1 measures throughput with no per-event timers. Pass 2 times each
    ``process`` call and records whether that event closed a window. Event
    tuples are built lazily outside the timed region.
    """
    cols = [c[:n] for c in columns]
    engine = make_engine()
    start = timer()
    for event in zip(*cols):
        engine.process(*event)
    engine.flush()
    seconds = (timer() - start) / 1e9

    engine = make_engine()
    lat = np.empty(n, dtype=np.int64)
    closes = np.zeros(n, dtype=bool)
    for i, event in enumerate(zip(*cols)):
        a = timer()
        out = engine.process(*event)
        b = timer()
        lat[i] = b - a
        closes[i] = bool(out.rows)
    overhead = timer_overhead(timer)
    return {"engine": "python", "n_events": n, "throughput_eps": n / seconds,
            "timer_overhead_ns": overhead, "latency_ns": latency_stats(lat, closes, overhead)}
```

- [ ] **Step 3: Write `scripts/bench_stream.py`**

```python
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
```

- [ ] **Step 4: Run tests, then the Python benchmark and profile**

Run: `python -m pytest tests/test_stream_bench.py -v` then `python -m pytest -q`. Expected: all pass.
Run: `python scripts/bench_stream.py --engines python --n 500000 --repeats 3 --profile`
Expected: prints a `cProfile` table (top 15 by own time), then the Python line with throughput and latencies. Record the top three hotspots and the numbers for the report. This writes `artifacts/stream_bench.json` (gitignored).

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/stream/bench.py scripts/bench_stream.py tests/test_stream_bench.py
git commit -m "$(cat <<'EOF'
feat(stream): latency/throughput harness and Python engine benchmark

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Java LightGBM evaluator, with Java build helpers

**Concept to explain:** a LightGBM model is a list of decision trees; the text file lists each tree as parallel arrays (`split_feature`, `threshold`, `left_child`, `right_child`, `leaf_value`). Walk from node 0: go left if the feature value is `<=` the threshold, else right. A negative child index `c` means "leaf number `~c`". For a 4-class model, tree `i` belongs to class `i mod 4`; a class's raw score is the sum of its trees' leaf values, and softmax turns the four raw scores into probabilities.

**Files:**
- Create: `java/.gitignore`, `java/src/sentinel/LgbmModel.java`, `java/src/sentinel/SelfTest.java`, `src/sentinel/stream/java.py`
- Test: `tests/test_java_parity.py` (first part)

- [ ] **Step 1: Write `java/.gitignore`**

```
out/
```

- [ ] **Step 2: Write `src/sentinel/stream/java.py`**

```python
"""Build and run the Java engine from Python."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

JAVA_DIR = Path(__file__).resolve().parents[3] / "java"
OUT_DIR = JAVA_DIR / "out"


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"{name} not found on PATH (a JDK is required)")
    return path


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(cmd)}\n{result.stdout}\n{result.stderr}")
    return result


def available() -> bool:
    """True if javac and java are on PATH."""
    return shutil.which("javac") is not None and shutil.which("java") is not None


def build() -> Path:
    """Compile every source under java/src into java/out; returns the class directory."""
    sources = sorted((JAVA_DIR / "src").rglob("*.java"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _run([_tool("javac"), "-d", str(OUT_DIR), *map(str, sources)])
    return OUT_DIR


def _java(main_class: str, *args: object, heap: str = "3g") -> list[str]:
    return [_tool("java"), f"-Xmx{heap}", "-cp", str(OUT_DIR), main_class, *map(str, args)]


def selftest(model: Path, fixture: Path) -> str:
    """Run the Java evaluator against a Python-made fixture; raises on any mismatch."""
    return _run(_java("sentinel.SelfTest", model, fixture)).stdout


def run_engine(events_bin: Path, config: Path, model: Path, out_dir: Path, n: int | None = None) -> None:
    """Run the Java engine over an events file; writes features.csv and alerts.csv into ``out_dir``."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    args = ["run", events_bin, config, model, out_dir] + ([n] if n is not None else [])
    _run(_java("sentinel.Main", *args))


def bench_engine(events_bin: Path, config: Path, model: Path, n: int) -> dict:
    """Run the Java benchmark and return its JSON result."""
    result = _run(_java("sentinel.Main", "bench", events_bin, config, model, n))
    return json.loads(result.stdout.strip().splitlines()[-1])
```

- [ ] **Step 3: Write the failing test** (`tests/test_java_parity.py`, first part)

```python
"""Java engine tests (skipped when no JDK is on PATH)."""
from __future__ import annotations

import numpy as np
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
```
Run: `python -m pytest tests/test_java_parity.py -v`. Expected: FAIL when compiling (no Java sources yet: `javac` gets no files) or a similar build error. That is the red step.

- [ ] **Step 4: Write `java/src/sentinel/LgbmModel.java`**

```java
package sentinel;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/** Evaluator for LightGBM multiclass models saved in the text format (numerical splits only). */
public final class LgbmModel {
    private static final double ZERO_THRESHOLD = 1e-35;
    private static final int MISSING_ZERO = 1;
    private static final int MISSING_NAN = 2;

    private static final class Tree {
        int numLeaves;
        int[] splitFeature = new int[0];
        double[] threshold = new double[0];
        int[] decisionType = new int[0];
        int[] left = new int[0];
        int[] right = new int[0];
        double[] leafValue = new double[0];

        double predict(double[] x) {
            if (numLeaves <= 1) {
                return leafValue[0];
            }
            int node = 0;
            while (node >= 0) {
                double v = x[splitFeature[node]];
                int type = decisionType[node];
                int missingType = (type >> 2) & 3;
                boolean defaultLeft = (type & 2) != 0;
                if (Double.isNaN(v) && missingType != MISSING_NAN) {
                    v = 0.0;
                }
                boolean goLeft;
                if ((missingType == MISSING_ZERO && Math.abs(v) <= ZERO_THRESHOLD)
                        || (missingType == MISSING_NAN && Double.isNaN(v))) {
                    goLeft = defaultLeft;
                } else {
                    goLeft = v <= threshold[node];
                }
                node = goLeft ? left[node] : right[node];
            }
            return leafValue[~node];
        }
    }

    private final List<Tree> trees = new ArrayList<>();
    private int numClass = 1;

    private LgbmModel() {
    }

    /** Parse a model file written by LightGBM's save_model. */
    public static LgbmModel load(Path path) throws IOException {
        LgbmModel model = new LgbmModel();
        Tree current = null;
        for (String raw : Files.readAllLines(path)) {
            String line = raw.trim();
            if (line.startsWith("Tree=")) {
                current = new Tree();
                model.trees.add(current);
                continue;
            }
            if (line.equals("end of trees")) {
                break;
            }
            int eq = line.indexOf('=');
            if (eq < 0) {
                continue;
            }
            String key = line.substring(0, eq);
            String value = line.substring(eq + 1);
            if (current == null) {
                if (key.equals("num_class")) {
                    model.numClass = Integer.parseInt(value);
                }
                continue;
            }
            switch (key) {
                case "num_leaves" -> current.numLeaves = Integer.parseInt(value);
                case "split_feature" -> current.splitFeature = ints(value);
                case "threshold" -> current.threshold = doubles(value);
                case "decision_type" -> current.decisionType = ints(value);
                case "left_child" -> current.left = ints(value);
                case "right_child" -> current.right = ints(value);
                case "leaf_value" -> current.leafValue = doubles(value);
                default -> { }
            }
        }
        for (Tree tree : model.trees) {
            for (int type : tree.decisionType) {
                if ((type & 1) != 0) {
                    throw new UnsupportedOperationException("categorical splits are not supported");
                }
            }
        }
        if (model.trees.isEmpty() || model.trees.size() % model.numClass != 0) {
            throw new IllegalStateException("unexpected tree count " + model.trees.size());
        }
        return model;
    }

    public int numClass() {
        return numClass;
    }

    /** Raw (pre-softmax) class scores into {@code out}; tree i contributes to class i mod numClass. */
    public void rawScores(double[] x, double[] out) {
        java.util.Arrays.fill(out, 0, numClass, 0.0);
        for (int i = 0; i < trees.size(); i++) {
            out[i % numClass] += trees.get(i).predict(x);
        }
    }

    /** Class probabilities into {@code out} (numerically stable softmax, as LightGBM computes it). */
    public void predict(double[] x, double[] out) {
        rawScores(x, out);
        double max = out[0];
        for (int k = 1; k < numClass; k++) {
            max = Math.max(max, out[k]);
        }
        double sum = 0.0;
        for (int k = 0; k < numClass; k++) {
            out[k] = Math.exp(out[k] - max);
            sum += out[k];
        }
        for (int k = 0; k < numClass; k++) {
            out[k] /= sum;
        }
    }

    private static int[] ints(String s) {
        if (s.isBlank()) {
            return new int[0];
        }
        String[] parts = s.trim().split(" ");
        int[] out = new int[parts.length];
        for (int i = 0; i < parts.length; i++) {
            out[i] = Integer.parseInt(parts[i]);
        }
        return out;
    }

    private static double[] doubles(String s) {
        if (s.isBlank()) {
            return new double[0];
        }
        String[] parts = s.trim().split(" ");
        double[] out = new double[parts.length];
        for (int i = 0; i < parts.length; i++) {
            out[i] = parseDouble(parts[i]);
        }
        return out;
    }

    private static double parseDouble(String s) {
        return switch (s) {
            case "inf", "+inf" -> Double.POSITIVE_INFINITY;
            case "-inf" -> Double.NEGATIVE_INFINITY;
            case "nan" -> Double.NaN;
            default -> Double.parseDouble(s);
        };
    }
}
```

- [ ] **Step 5: Write `java/src/sentinel/SelfTest.java`**

```java
package sentinel;

import java.nio.file.Files;
import java.nio.file.Path;

/** Checks the tree evaluator against a fixture: each line is 22 features then 4 probabilities. */
public final class SelfTest {
    public static void main(String[] args) throws Exception {
        LgbmModel model = LgbmModel.load(Path.of(args[0]));
        double[] p = new double[model.numClass()];
        int cases = 0;
        int bad = 0;
        for (String line : Files.readAllLines(Path.of(args[1]))) {
            if (line.isBlank()) {
                continue;
            }
            String[] f = line.split(",");
            double[] x = new double[f.length - model.numClass()];
            for (int i = 0; i < x.length; i++) {
                x[i] = Double.parseDouble(f[i]);
            }
            model.predict(x, p);
            for (int k = 0; k < p.length; k++) {
                double expected = Double.parseDouble(f[x.length + k]);
                if (Math.abs(p[k] - expected) > 1e-9 * Math.max(1.0, Math.abs(expected))) {
                    bad++;
                }
            }
            cases++;
        }
        System.out.println(cases + " cases, " + bad + " mismatches");
        if (bad > 0 || cases == 0) {
            System.exit(1);
        }
    }
}
```

- [ ] **Step 6: Run the test and commit**

Run: `python -m pytest tests/test_java_parity.py -v` then `python -m pytest -q`. Expected: pass. If the Java evaluator disagrees with LightGBM, print one mismatching case (Python probabilities vs Java) and check, in this order: child-index sign convention, the decision-type bit layout, class assignment `i mod numClass`. Do not loosen the 1e-9 tolerance.
```bash
git add java src/sentinel/stream/java.py tests/test_java_parity.py
git commit -m "$(cat <<'EOF'
feat(java): LightGBM text-model evaluator, verified against Python probabilities

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Java engine (config, extractor port, engine, reader, main)

**Concept to explain:** the Java feature extractor is a line-by-line port of the Python one: same per-trader live-order table, same window counters, same close-on-next-window rule, same arithmetic in the same order (so floating-point results match). Correctness is proved in Task 6 by running both on the same events; nothing here is trusted until then.

**Files:** Create `java/src/sentinel/{Config,FeatureExtractor,Engine,EventReader,Main}.java`

- [ ] **Step 1: Write `Config.java`**

```java
package sentinel;

import java.io.IOException;
import java.io.Reader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Properties;

/** engine.properties: window length, model thresholds and rules. */
public final class Config {
    /** One threshold test on one feature. */
    public static final class Condition {
        final int feature;
        final boolean atLeast;
        final double threshold;

        Condition(int feature, boolean atLeast, double threshold) {
            this.feature = feature;
            this.atLeast = atLeast;
            this.threshold = threshold;
        }

        boolean holds(double[] x) {
            return atLeast ? x[feature] >= threshold : x[feature] <= threshold;
        }
    }

    /** A conjunction ("all") or disjunction ("any") of conditions. */
    public static final class Rule {
        public final String detector;
        public final String kind;
        final boolean all;
        final Condition[] conditions;

        Rule(String detector, String kind, boolean all, Condition[] conditions) {
            this.detector = detector;
            this.kind = kind;
            this.all = all;
            this.conditions = conditions;
        }

        boolean flags(double[] x) {
            if (all) {
                for (Condition c : conditions) {
                    if (!c.holds(x)) {
                        return false;
                    }
                }
                return true;
            }
            for (Condition c : conditions) {
                if (c.holds(x)) {
                    return true;
                }
            }
            return false;
        }
    }

    public final int windowS;
    public final int nInstruments;
    /** Alert thresholds indexed by class (1 = SPOOFING, 2 = LAYERING, 3 = WASH); index 0 unused. */
    public final double[] thresholds = new double[4];
    public final List<Rule> rules = new ArrayList<>();

    private Config(int windowS, int nInstruments) {
        this.windowS = windowS;
        this.nInstruments = nInstruments;
    }

    public static Config load(Path path) throws IOException {
        Properties p = new Properties();
        try (Reader in = Files.newBufferedReader(path)) {
            p.load(in);
        }
        String[] features = p.getProperty("features").split(",");
        if (!java.util.Arrays.equals(features, FeatureExtractor.FEATURE_NAMES)) {
            throw new IllegalStateException("feature order in config differs from the Java extractor");
        }
        Config c = new Config(Integer.parseInt(p.getProperty("window_s")),
                p.getProperty("instruments").split(",").length);
        String[] kinds = {"", "SPOOFING", "LAYERING", "WASH"};
        for (int k = 1; k < 4; k++) {
            c.thresholds[k] = Double.parseDouble(p.getProperty("threshold." + kinds[k]));
        }
        int count = Integer.parseInt(p.getProperty("rule.count"));
        for (int i = 0; i < count; i++) {
            String[] parts = p.getProperty("rule." + i + ".conditions").split(";");
            Condition[] conditions = new Condition[parts.length];
            for (int j = 0; j < parts.length; j++) {
                conditions[j] = parseCondition(parts[j]);
            }
            c.rules.add(new Rule(p.getProperty("rule." + i + ".detector"), p.getProperty("rule." + i + ".kind"),
                    p.getProperty("rule." + i + ".mode").equals("all"), conditions));
        }
        return c;
    }

    private static Condition parseCondition(String text) {
        boolean atLeast = text.contains(">=");
        int at = text.indexOf(atLeast ? ">=" : "<=");
        String name = text.substring(0, at);
        double threshold = Double.parseDouble(text.substring(at + 2));
        for (int i = 0; i < FeatureExtractor.FEATURE_NAMES.length; i++) {
            if (FeatureExtractor.FEATURE_NAMES[i].equals(name)) {
                return new Condition(i, atLeast, threshold);
            }
        }
        throw new IllegalArgumentException("unknown feature in rule: " + name);
    }
}
```

- [ ] **Step 2: Write `FeatureExtractor.java`**

```java
package sentinel;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;

/**
 * Streaming per-trader-window feature extractor: a port of sentinel.features.extractor (Python).
 * Every feature is computed with the same operations in the same order as the Python reference.
 */
public final class FeatureExtractor {
    public static final String[] FEATURE_NAMES = {
        "n_new", "n_cancel", "n_modify", "n_fills", "order_to_trade_ratio",
        "cancel_rate", "cancel_within_1s_share", "cancel_within_3s_share", "median_cancelled_lifetime_s",
        "total_new_qty", "total_fill_qty", "max_order_qty", "max_qty_over_best", "peak_resting_over_depth5",
        "max_live_levels_same_side", "max_cancel_burst_100ms",
        "opposite_fill_share", "signed_mid_drift_ticks",
        "net_over_gross", "top_counterparty_share", "n_counterparties", "roundtrip_counterparty_share",
    };
    public static final int N_FEATURES = FEATURE_NAMES.length;

    private static final long NS_PER_SEC = 1_000_000_000L;
    private static final long BURST_NS = 100_000_000L;
    private static final int NEW = 0;
    private static final int CANCEL = 1;
    private static final int MODIFY = 2;
    private static final int EXEC = 3;
    private static final int BID = 1;

    /** Features of one trader in one closed window. */
    public static final class Row {
        public final long trader;
        public final int instrument;
        public final long window;
        public final double[] values;

        Row(long trader, int instrument, long window, double[] values) {
            this.trader = trader;
            this.instrument = instrument;
            this.window = window;
            this.values = values;
        }
    }

    private static final class Live {
        final long ts;
        final int side;
        long price;
        long qty;

        Live(long ts, int side, long price, long qty) {
            this.ts = ts;
            this.side = side;
            this.price = price;
            this.qty = qty;
        }
    }

    private static final class Win {
        long nNew;
        long nNewLimit;
        long nCancel;
        long nModify;
        long nFills;
        long newQty;
        long fillBuy;
        long fillSell;
        long maxQty;
        long quick1;
        long quick3;
        long maxLevels;
        long maxBurst;
        long peakBid;
        long peakAsk;
        double maxQtyOverBest;
        double peakRatio;
        double firstMid;
        double lastMid;
        boolean hasMid;
        double[] lifetimes = new double[8];
        int nLifetimes;
        final HashMap<Long, Long> cpBuy = new HashMap<>();
        final HashMap<Long, Long> cpSell = new HashMap<>();

        void addLifetime(double v) {
            if (nLifetimes == lifetimes.length) {
                lifetimes = Arrays.copyOf(lifetimes, nLifetimes * 2);
            }
            lifetimes[nLifetimes++] = v;
        }
    }

    private static final class Trader {
        final HashMap<Long, Live> live = new HashMap<>();
        long restingBid;
        long restingAsk;
        final HashMap<Long, Integer> levelsBid = new HashMap<>();
        final HashMap<Long, Integer> levelsAsk = new HashMap<>();
        final ArrayDeque<Long> cancels = new ArrayDeque<>();
        Win win;

        HashMap<Long, Integer> levels(int side) {
            return side == BID ? levelsBid : levelsAsk;
        }

        long resting(int side) {
            return side == BID ? restingBid : restingAsk;
        }

        void addResting(int side, long delta) {
            if (side == BID) {
                restingBid += delta;
            } else {
                restingAsk += delta;
            }
        }
    }

    private static final class Instrument {
        boolean hasWindow;
        long window;
        long lastTs = -1;
        final HashMap<Long, Trader> traders = new HashMap<>();
        final ArrayList<Long> active = new ArrayList<>();
    }

    private static final List<Row> NONE = Collections.emptyList();

    private final long windowNs;
    private final Instrument[] instruments;

    public FeatureExtractor(int windowS, int nInstruments) {
        this.windowNs = windowS * NS_PER_SEC;
        this.instruments = new Instrument[nInstruments];
        for (int i = 0; i < nInstruments; i++) {
            instruments[i] = new Instrument();
        }
    }

    /** Process one event; returns the rows of any window this event closed (usually empty). */
    public List<Row> process(long ts, int instrument, int etype, long orderId, long traderId, long counterparty,
                             int side, long price, long qty, long bestBid, long bestAsk, long bidQty1,
                             long askQty1, long bidDepth5, long askDepth5) {
        Instrument inst = instruments[instrument];
        if (ts < inst.lastTs) {
            throw new IllegalStateException("out-of-order event: " + ts + " < " + inst.lastTs);
        }
        inst.lastTs = ts;
        long window = ts / windowNs;
        List<Row> closed = NONE;
        if (!inst.hasWindow) {
            inst.window = window;
            inst.hasWindow = true;
        } else if (window > inst.window) {
            closed = close(inst, instrument);
            inst.window = window;
        }

        Trader t = inst.traders.get(traderId);
        if (t == null) {
            t = new Trader();
            inst.traders.put(traderId, t);
        }
        Win w = t.win;
        if (w == null) {
            w = new Win();
            t.win = w;
            inst.active.add(traderId);
        }
        if (bestBid != 0 && bestAsk != 0) {
            double mid = (bestBid + bestAsk) / 2.0;
            if (!w.hasMid) {
                w.firstMid = mid;
                w.hasMid = true;
            }
            w.lastMid = mid;
        }

        long ownBest = side == BID ? bidQty1 : askQty1;
        long ownDepth = side == BID ? bidDepth5 : askDepth5;
        switch (etype) {
            case NEW -> onNew(t, w, ts, orderId, side, price, qty, ownBest, ownDepth);
            case CANCEL -> onCancel(t, w, ts, orderId);
            case MODIFY -> onModify(t, w, orderId, price, qty, ownDepth);
            case EXEC -> onExec(t, w, orderId, side, qty, counterparty);
            default -> throw new IllegalArgumentException("bad event type " + etype);
        }
        return closed;
    }

    /** Close every open window (end of stream). */
    public List<Row> flush() {
        List<Row> rows = new ArrayList<>();
        for (int i = 0; i < instruments.length; i++) {
            Instrument inst = instruments[i];
            if (inst.hasWindow && !inst.active.isEmpty()) {
                rows.addAll(close(inst, i));
            }
        }
        return rows;
    }

    private void onNew(Trader t, Win w, long ts, long orderId, int side, long price, long qty,
                       long ownBest, long ownDepth) {
        w.nNew++;
        if (price <= 0) {
            return;
        }
        w.nNewLimit++;
        w.newQty += qty;
        w.maxQty = Math.max(w.maxQty, qty);
        w.maxQtyOverBest = Math.max(w.maxQtyOverBest, (double) qty / Math.max(1L, ownBest));
        t.live.put(orderId, new Live(ts, side, price, qty));
        t.addResting(side, qty);
        levelUp(t, side, price);
        notePeaks(t, w, side, ownDepth);
    }

    private void onCancel(Trader t, Win w, long ts, long orderId) {
        w.nCancel++;
        Live live = removeLive(t, orderId);
        if (live != null) {
            double lifetime = (double) (ts - live.ts) / NS_PER_SEC;
            w.addLifetime(lifetime);
            if (lifetime <= 1.0) {
                w.quick1++;
            }
            if (lifetime <= 3.0) {
                w.quick3++;
            }
        }
        ArrayDeque<Long> recent = t.cancels;
        recent.addLast(ts);
        while (ts - recent.peekFirst() > BURST_NS) {
            recent.pollFirst();
        }
        w.maxBurst = Math.max(w.maxBurst, recent.size());
    }

    private void onModify(Trader t, Win w, long orderId, long price, long qty, long ownDepth) {
        w.nModify++;
        Live live = t.live.get(orderId);
        if (live == null) {
            return;
        }
        t.addResting(live.side, qty - live.qty);
        if (price != live.price) {
            levelDown(t, live.side, live.price);
            levelUp(t, live.side, price);
            live.price = price;
        }
        live.qty = qty;
        notePeaks(t, w, live.side, ownDepth);
    }

    private void onExec(Trader t, Win w, long orderId, int side, long qty, long counterparty) {
        w.nFills++;
        if (side == BID) {
            w.fillBuy += qty;
            w.cpBuy.merge(counterparty, qty, Long::sum);
        } else {
            w.fillSell += qty;
            w.cpSell.merge(counterparty, qty, Long::sum);
        }
        Live live = t.live.get(orderId);
        if (live != null) {
            live.qty -= qty;
            t.addResting(live.side, -qty);
            if (live.qty <= 0) {
                removeLive(t, orderId);
            }
        }
    }

    private static void levelUp(Trader t, int side, long price) {
        t.levels(side).merge(price, 1, Integer::sum);
    }

    private static void levelDown(Trader t, int side, long price) {
        HashMap<Long, Integer> levels = t.levels(side);
        int n = levels.get(price);
        if (n > 1) {
            levels.put(price, n - 1);
        } else {
            levels.remove(price);
        }
    }

    private static Live removeLive(Trader t, long orderId) {
        Live live = t.live.remove(orderId);
        if (live != null) {
            t.addResting(live.side, -live.qty);
            levelDown(t, live.side, live.price);
        }
        return live;
    }

    private static void notePeaks(Trader t, Win w, int side, long ownDepth) {
        long resting = t.resting(side);
        if (side == BID) {
            w.peakBid = Math.max(w.peakBid, resting);
        } else {
            w.peakAsk = Math.max(w.peakAsk, resting);
        }
        w.peakRatio = Math.max(w.peakRatio, (double) resting / Math.max(1L, ownDepth));
        w.maxLevels = Math.max(w.maxLevels, t.levels(side).size());
    }

    private List<Row> close(Instrument inst, int instrument) {
        long[] ids = new long[inst.active.size()];
        for (int i = 0; i < ids.length; i++) {
            ids[i] = inst.active.get(i);
        }
        Arrays.sort(ids);
        List<Row> rows = new ArrayList<>(ids.length);
        for (long id : ids) {
            Trader t = inst.traders.get(id);
            rows.add(new Row(id, instrument, inst.window, features(t.win)));
            t.win = null;
        }
        inst.active.clear();
        return rows;
    }

    private static double[] features(Win w) {
        double limitDen = Math.max(1L, w.nNewLimit);
        long totalFill = w.fillBuy + w.fillSell;
        int peakSide = w.peakBid >= w.peakAsk ? BID : -1;
        boolean hasPeak = (w.peakBid + w.peakAsk) > 0;
        double opposite = 0.0;
        if (hasPeak && totalFill != 0) {
            long oppositeQty = peakSide == BID ? w.fillSell : w.fillBuy;
            opposite = (double) oppositeQty / totalFill;
        }
        double drift = 0.0;
        if (hasPeak && w.hasMid) {
            drift = (peakSide == BID ? 1 : -1) * (w.lastMid - w.firstMid);
        }
        HashMap<Long, Long> totals = new HashMap<>(w.cpBuy);
        w.cpSell.forEach((cp, q) -> totals.merge(cp, q, Long::sum));
        long roundtrip = 0;
        for (Long cp : w.cpBuy.keySet()) {
            if (w.cpSell.containsKey(cp)) {
                roundtrip += totals.get(cp);
            }
        }
        long topCounterparty = 0;
        for (long q : totals.values()) {
            topCounterparty = Math.max(topCounterparty, q);
        }
        double median = -1.0;
        if (w.nLifetimes > 0) {
            double[] sorted = Arrays.copyOf(w.lifetimes, w.nLifetimes);
            Arrays.sort(sorted);
            int mid = sorted.length / 2;
            median = sorted.length % 2 == 1 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
        }
        double[] f = new double[N_FEATURES];
        f[0] = w.nNew;
        f[1] = w.nCancel;
        f[2] = w.nModify;
        f[3] = w.nFills;
        f[4] = (double) w.nNew / Math.max(1L, w.nFills);
        f[5] = (double) w.nCancel / Math.max(1L, w.nNew);
        f[6] = Math.min(1.0, w.quick1 / limitDen);
        f[7] = Math.min(1.0, w.quick3 / limitDen);
        f[8] = median;
        f[9] = w.newQty;
        f[10] = totalFill;
        f[11] = w.maxQty;
        f[12] = w.maxQtyOverBest;
        f[13] = w.peakRatio;
        f[14] = w.maxLevels;
        f[15] = w.maxBurst;
        f[16] = opposite;
        f[17] = drift;
        f[18] = totalFill != 0 ? (double) Math.abs(w.fillBuy - w.fillSell) / totalFill : 1.0;
        f[19] = totalFill != 0 && !totals.isEmpty() ? (double) topCounterparty / totalFill : 0.0;
        f[20] = totals.size();
        f[21] = totalFill != 0 ? (double) roundtrip / totalFill : 0.0;
        return f;
    }
}
```

- [ ] **Step 3: Write `Engine.java`**

```java
package sentinel;

import java.util.ArrayList;
import java.util.List;

/** Feature extractor plus LightGBM scoring and rules, one event at a time. */
public final class Engine {
    public static final String[] CLASS_NAMES = {"NORMAL", "SPOOFING", "LAYERING", "WASH"};

    /** Receives every scored feature row with its class probabilities (used by the run mode). */
    public interface RowSink {
        void accept(FeatureExtractor.Row row, double[] proba);
    }

    /** A flagged trader-window from one detector. */
    public static final class Alert {
        public final String detector;
        public final String kind;
        public final long trader;
        public final int instrument;
        public final long window;
        public final double score;

        Alert(String detector, String kind, long trader, int instrument, long window, double score) {
            this.detector = detector;
            this.kind = kind;
            this.trader = trader;
            this.instrument = instrument;
            this.window = window;
            this.score = score;
        }
    }

    private final FeatureExtractor extractor;
    private final LgbmModel model;
    private final Config config;
    private final RowSink sink;
    private final double[] proba = new double[4];
    public final List<Alert> alerts = new ArrayList<>();
    public long rowsScored;

    public Engine(Config config, LgbmModel model, RowSink sink) {
        this.config = config;
        this.model = model;
        this.sink = sink;
        this.extractor = new FeatureExtractor(config.windowS, config.nInstruments);
    }

    /** Process one event; returns the number of feature rows scored (non-zero when a window closed). */
    public int process(long ts, int instrument, int etype, long orderId, long traderId, long counterparty,
                       int side, long price, long qty, long bestBid, long bestAsk, long bidQty1,
                       long askQty1, long bidDepth5, long askDepth5) {
        List<FeatureExtractor.Row> closed = extractor.process(ts, instrument, etype, orderId, traderId,
                counterparty, side, price, qty, bestBid, bestAsk, bidQty1, askQty1, bidDepth5, askDepth5);
        if (closed.isEmpty()) {
            return 0;
        }
        score(closed);
        return closed.size();
    }

    /** Close and score every open window (end of stream). */
    public int flush() {
        List<FeatureExtractor.Row> closed = extractor.flush();
        if (!closed.isEmpty()) {
            score(closed);
        }
        return closed.size();
    }

    private void score(List<FeatureExtractor.Row> rows) {
        for (FeatureExtractor.Row row : rows) {
            model.predict(row.values, proba);
            if (sink != null) {
                sink.accept(row, proba);
            }
            int type = 0;
            double best = -1.0;
            for (int k = 1; k < 4; k++) {
                if (proba[k] >= config.thresholds[k] && proba[k] > best) {
                    type = k;
                    best = proba[k];
                }
            }
            if (type != 0) {
                alerts.add(new Alert("model", CLASS_NAMES[type], row.trader, row.instrument, row.window, proba[type]));
            }
            for (Config.Rule rule : config.rules) {
                if (rule.flags(row.values)) {
                    alerts.add(new Alert(rule.detector, rule.kind, row.trader, row.instrument, row.window, 1.0));
                }
            }
        }
        rowsScored += rows.size();
    }
}
```

- [ ] **Step 4: Write `EventReader.java`**

```java
package sentinel;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.MappedByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;

/** Reads events.bin: 8-byte magic, int64 count, then 15 little-endian int64 fields per event. */
public final class EventReader {
    public static final int FIELDS = 15;

    /** All events in memory, row-major. */
    public static final class Events {
        public final long[] data;
        public final int n;

        Events(long[] data, int n) {
            this.data = data;
            this.n = n;
        }
    }

    private EventReader() {
    }

    public static Events read(Path path, int maxEvents) throws IOException {
        try (FileChannel ch = FileChannel.open(path, StandardOpenOption.READ)) {
            ByteBuffer header = ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN);
            ch.read(header, 0);
            header.flip();
            byte[] magic = new byte[8];
            header.get(magic);
            if (!new String(magic, StandardCharsets.US_ASCII).equals("SNTLEV01")) {
                throw new IOException("not an events.bin file: " + path);
            }
            long total = header.getLong();
            int n = (int) Math.min(total, (long) maxEvents);
            long[] data = new long[n * FIELDS];
            MappedByteBuffer buffer = ch.map(FileChannel.MapMode.READ_ONLY, 16, (long) n * FIELDS * 8);
            buffer.order(ByteOrder.LITTLE_ENDIAN).asLongBuffer().get(data);
            return new Events(data, n);
        }
    }
}
```

- [ ] **Step 5: Write `Main.java`**

```java
package sentinel;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.Locale;

/**
 * Command line entry point.
 *   run   events.bin engine.properties model.txt out_dir [n]   writes features.csv and alerts.csv
 *   bench events.bin engine.properties model.txt [n]           prints one JSON line of timings
 */
public final class Main {
    private static final int F = EventReader.FIELDS;

    public static void main(String[] args) throws Exception {
        if (args.length == 0) {
            usage();
        }
        switch (args[0]) {
            case "run" -> run(args);
            case "bench" -> bench(args);
            default -> usage();
        }
    }

    private static void usage() {
        System.err.println("usage: run events.bin engine.properties model.txt out_dir [n] | "
                + "bench events.bin engine.properties model.txt [n]");
        System.exit(2);
    }

    private static Engine feed(EventReader.Events ev, Config cfg, LgbmModel model, Engine.RowSink sink) {
        Engine engine = new Engine(cfg, model, sink);
        long[] d = ev.data;
        for (int i = 0; i < ev.n; i++) {
            int o = i * F;
            engine.process(d[o], (int) d[o + 1], (int) d[o + 2], d[o + 3], d[o + 4], d[o + 5], (int) d[o + 6],
                    d[o + 7], d[o + 8], d[o + 9], d[o + 10], d[o + 11], d[o + 12], d[o + 13], d[o + 14]);
        }
        engine.flush();
        return engine;
    }

    private static void run(String[] a) throws Exception {
        Config cfg = Config.load(Path.of(a[2]));
        LgbmModel model = LgbmModel.load(Path.of(a[3]));
        Path out = Path.of(a[4]);
        Files.createDirectories(out);
        EventReader.Events ev = EventReader.read(Path.of(a[1]), a.length > 5 ? Integer.parseInt(a[5]) : Integer.MAX_VALUE);
        try (BufferedWriter features = Files.newBufferedWriter(out.resolve("features.csv"))) {
            features.write("trader,instrument,window," + String.join(",", FeatureExtractor.FEATURE_NAMES)
                    + ",p0,p1,p2,p3\n");
            Engine.RowSink sink = (row, proba) -> {
                StringBuilder sb = new StringBuilder();
                sb.append(row.trader).append(',').append(row.instrument).append(',').append(row.window);
                for (double v : row.values) {
                    sb.append(',').append(v);
                }
                for (double p : proba) {
                    sb.append(',').append(p);
                }
                try {
                    features.write(sb.append('\n').toString());
                } catch (IOException e) {
                    throw new RuntimeException(e);
                }
            };
            Engine engine = feed(ev, cfg, model, sink);
            try (BufferedWriter alerts = Files.newBufferedWriter(out.resolve("alerts.csv"))) {
                alerts.write("detector,kind,trader,instrument,window,score\n");
                for (Engine.Alert x : engine.alerts) {
                    alerts.write(x.detector + "," + x.kind + "," + x.trader + "," + x.instrument + ","
                            + x.window + "," + x.score + "\n");
                }
            }
        }
    }

    private static void bench(String[] a) throws Exception {
        Config cfg = Config.load(Path.of(a[2]));
        LgbmModel model = LgbmModel.load(Path.of(a[3]));
        EventReader.Events ev = EventReader.read(Path.of(a[1]), a.length > 4 ? Integer.parseInt(a[4]) : Integer.MAX_VALUE);

        for (int warmup = 0; warmup < 2; warmup++) {
            feed(ev, cfg, model, null);  // unmeasured: lets the JIT compile the hot paths
        }
        long t0 = System.nanoTime();
        Engine measured = feed(ev, cfg, model, null);
        double seconds = (System.nanoTime() - t0) / 1e9;

        Engine engine = new Engine(cfg, model, null);
        long[] d = ev.data;
        long[] lat = new long[ev.n];
        boolean[] closes = new boolean[ev.n];
        for (int i = 0; i < ev.n; i++) {
            int o = i * F;
            long start = System.nanoTime();
            int closed = engine.process(d[o], (int) d[o + 1], (int) d[o + 2], d[o + 3], d[o + 4], d[o + 5],
                    (int) d[o + 6], d[o + 7], d[o + 8], d[o + 9], d[o + 10], d[o + 11], d[o + 12], d[o + 13], d[o + 14]);
            long end = System.nanoTime();
            lat[i] = end - start;
            closes[i] = closed > 0;
        }
        long overhead = timerOverhead();
        int nClose = 0;
        for (boolean c : closes) {
            if (c) {
                nClose++;
            }
        }
        long[] all = new long[ev.n];
        long[] close = new long[nClose];
        long[] other = new long[ev.n - nClose];
        int ci = 0;
        int oi = 0;
        for (int i = 0; i < ev.n; i++) {
            all[i] = Math.max(0, lat[i] - overhead);
            if (closes[i]) {
                close[ci++] = all[i];
            } else {
                other[oi++] = all[i];
            }
        }
        System.out.println(String.format(Locale.ROOT,
                "{\"engine\":\"java\",\"n_events\":%d,\"throughput_eps\":%.1f,\"timer_overhead_ns\":%d,"
                        + "\"rows_scored\":%d,\"n_alerts\":%d,\"latency_ns\":{\"overall\":%s,\"window_close\":%s,\"other\":%s}}",
                ev.n, ev.n / seconds, overhead, measured.rowsScored, measured.alerts.size(),
                summary(all), summary(close), summary(other)));
    }

    private static long timerOverhead() {
        long[] diffs = new long[200_000];
        for (int i = 0; i < diffs.length; i++) {
            long x = System.nanoTime();
            long y = System.nanoTime();
            diffs[i] = y - x;
        }
        Arrays.sort(diffs);
        return diffs[diffs.length / 2];
    }

    /** JSON object with n, median, p99, p999, max using nearest-rank percentiles. */
    private static String summary(long[] values) {
        if (values.length == 0) {
            return "{\"n\":0,\"median\":0,\"p99\":0,\"p999\":0,\"max\":0}";
        }
        long[] s = values.clone();
        Arrays.sort(s);
        return String.format(Locale.ROOT, "{\"n\":%d,\"median\":%d,\"p99\":%d,\"p999\":%d,\"max\":%d}",
                s.length, nearestRank(s, 0.5), nearestRank(s, 0.99), nearestRank(s, 0.999), s[s.length - 1]);
    }

    private static long nearestRank(long[] sorted, double p) {
        int index = Math.max(0, (int) Math.ceil(p * sorted.length) - 1);
        return sorted[index];
    }
}
```

- [ ] **Step 6: Compile**

Run: `python -c "from sentinel.stream import java; print(java.build())"`
Expected: prints the `java/out` path with no compiler errors. Fix any compile error (keep the port faithful to the Python; do not simplify feature logic).

- [ ] **Step 7: Commit**

```bash
git add java
git commit -m "$(cat <<'EOF'
feat(java): streaming engine (extractor port, rules, model scoring, run/bench modes)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Parity between the Python and Java engines

**Concept to explain:** parity is the argument that the Java port is the *same* detector. We run both engines on identical events and demand identical feature rows (integer counts exactly, other floats within 1e-9 relative), identical class probabilities (1e-9), and identical alert sets for all four detectors. Any difference is a bug in the port.

**Files:** Create `src/sentinel/stream/parity.py`, `scripts/parity_check.py`; Modify `tests/test_java_parity.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_java_parity.py`)

```python
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
```
Run: `python -m pytest tests/test_java_parity.py -v`. Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.stream.parity'`.

- [ ] **Step 2: Write `src/sentinel/stream/parity.py`**

```python
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
```

- [ ] **Step 3: Run the test; debug real mismatches**

Run: `python -m pytest tests/test_java_parity.py -v`
Expected: PASS. If it fails, the report lists the first differing rows per column. Diagnose by comparing the specific trader-window's events in Python and Java (the Python extractor is the reference and has 19 hand-checked tests). Likely causes: an operation-order difference in a float feature, the even-count median, iteration order over a hash map affecting a *sum* (integers are order-independent; floats are not, so check any float accumulation), or the sign/side handling of `peakSide`. Fix the Java port; never relax `RTOL` or the exact-equality set.

- [ ] **Step 4: Write `scripts/parity_check.py` and run it on the real model and a full simulation**

```python
"""Compare the Python and Java engines on a real event log with the trained models.

python scripts/parity_check.py [--events data/sim_seed9/events.parquet] [--n N]
Exit code 1 if any mismatch is found. Never used with the final test simulation.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from sentinel.stream.engine import load_engine_parts
from sentinel.stream.parity import compare_engines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, default=Path("data/sim_seed9/events.parquet"))
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    parser.add_argument("--n", type=int, default=None)
    parser.add_argument("--workdir", type=Path, default=Path("artifacts/parity"))
    args = parser.parse_args()
    if args.events.parent.name.startswith("test_"):
        sys.exit("refusing to run on the final test simulation")

    booster, thresholds, rule_sets = load_engine_parts(args.model_dir)
    report = compare_engines(pd.read_parquet(args.events), booster, thresholds, rule_sets, args.workdir, args.n)
    print(f"feature rows compared: {report.n_rows:,}; alerts (all detectors): {report.n_alerts:,}")
    if report.mismatches:
        print("PARITY FAILED:")
        print("\n".join(f"  - {m}" for m in report.mismatches))
        sys.exit(1)
    print("PARITY OK: identical feature rows, probabilities and alert sets")


if __name__ == "__main__":
    main()
```
Run: `python scripts/parity_check.py` (full seed-9 stream, 1.6M events, real models).
Expected: `PARITY OK`, with the row and alert counts printed. Report them. If it fails, fix the Java port and rerun; the pytest parity test must also stay green.

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/stream/parity.py scripts/parity_check.py tests/test_java_parity.py
git commit -m "$(cat <<'EOF'
feat(stream): Python/Java parity check on features, probabilities and alerts

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Benchmark both engines, one profile-guided Java fix, final numbers

**Files:** `scripts/bench_stream.py` (already written), Java sources (optimization), no new files

- [ ] **Step 1: Baseline benchmark of both engines**

Run: `python scripts/bench_stream.py --engines python,java --n 500000 --repeats 3`
Expected: prints two result blocks (Python and Java), writes `artifacts/stream_bench.json`. Copy the printed lines and the `environment` block into your report. Keep the JSON as `artifacts/stream_bench_v1.json` (`cp`) for the before/after comparison.

- [ ] **Step 2: Profile the Java engine**

Run (after `python -c "from sentinel.stream import java; java.build()"` and with `artifacts/java_bench/` created by Step 1):
```bash
java -Xmx3g -XX:StartFlightRecording=filename=artifacts/java_bench/profile.jfr,settings=profile -cp java/out sentinel.Main bench artifacts/java_bench/events.bin artifacts/java_bench/engine.properties artifacts/java_bench/lgbm.txt 500000
jfr view hot-methods artifacts/java_bench/profile.jfr
```
Report the top five methods by sample share.

- [ ] **Step 3: Apply ONE profile-guided fix**

Choose the single largest hotspot from Step 2 and fix only that. Typical candidates, in likely order: boxed `HashMap<Long, ...>` lookups and `Long`/`Integer` allocation for per-trader tables (replace with a primitive open-addressing map for the hot table, or cache the `Trader` object); per-event allocation (`Live` objects, `ArrayList` per window close); repeated `HashMap.merge` boxing. Constraints: keep the Python-equivalent arithmetic and order of operations, change one thing, and do not touch the model evaluator unless it is the hotspot.

- [ ] **Step 3b: Re-verify parity, then re-benchmark**

Run: `python -m pytest tests/test_java_parity.py -q`, then `python scripts/parity_check.py` (both must stay green), then `python scripts/bench_stream.py --engines java --n 500000 --repeats 3` and save as `artifacts/stream_bench_v2.json`.
If the fix produced no measurable improvement (median throughput within run-to-run spread), say so honestly and revert it.

- [ ] **Step 4: Commit the optimization (if kept)**

```bash
git add java
git commit -m "$(cat <<'EOF'
perf(java): <name the hotspot and the change> (parity re-verified)

<one line with measured throughput before/after>

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Report**

Give the user a table with: Python throughput; Java v1 throughput; Java v2 throughput; and for each, median, p99, p99.9, max latency overall, plus the window-close versus ordinary split, the run-to-run spread over the 3 repeats, the environment block, the Python `cProfile` top hotspots and the Java hotspots. State the speedups as measured ratios. Do not round in your favor, and flag anything surprising (for example a p99.9 dominated by window-close events, or Java GC pauses in the max).

---

## Self-review (against the spec)

**Spec coverage**
- Components: Python engine, export, bench, java helpers, parity (Tasks 1-3, 6); Java LgbmModel, Config, FeatureExtractor, Engine, EventReader, Main, SelfTest (Tasks 4-5).
- Interchange (binary events with magic, count, 15 int64 fields; properties config): Task 1. Parity contract (row keys, exact counts, 1e-9 floats and probabilities, identical alert sets, model fixture): Tasks 4 and 6.
- Benchmark protocol (in-process per-event timing, throughput pass and latency pass, Java warmup, overhead subtracted, window-close split, nearest-rank percentiles in both languages, environment recorded, Python profile, one profile-guided Java fix with parity re-check): Tasks 3, 5, 7.
- Seed 100 is not used: `parity_check.py` refuses `test_*` directories; the bench script defaults to `sim_seed7`.
- Explanations stay out of the hot path: the engines emit `StreamAlert`/`Alert` without SHAP.

**Placeholder scan:** the only non-code step is Task 7 Step 3 (the fix depends on the measured hotspot), and it states the constraints and acceptance test (parity re-run, measured gain or revert). The commit message there names fields to be filled with real measurements.

**Consistency:** `process` argument order (`ts, instrument, etype, order_id, trader_id, counterparty_id, side, price, qty, best_bid, best_ask, bid_qty1, ask_qty1, bid_depth5, ask_depth5`) matches Python `event_columns`, `events_to_array` field order and Java `EventReader`/`Main.feed`. `FEATURE_NAMES` order is identical in Python and Java (verified at config load). Detector names `model`, `textbook`, `tuned`, `generic` are the same in Python alerts and Java alerts.

**Known risks the executor should expect:** floating-point parity of float features (Task 6 debugging); LightGBM text-format details (Task 4); a p99.9 and max dominated by window-close events and GC (report, do not hide); Python memory for 500k pre-built column lists (~300 MB; reduce `--n` if needed and say so).
