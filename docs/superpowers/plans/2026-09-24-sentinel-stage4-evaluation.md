# Sentinel Stage 4: Final Evaluation and RESULTS.md Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Commit rule:** every commit message ends with the line `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` (use a heredoc).
>
> **HARD RULE for Tasks 0-5:** never load, open or evaluate `data/test_seed100`. Development and rehearsal use `data/rehearsal_seed200` only. Task 6 (the single final run) is executed by the controller, not by a subagent.

**Goal:** Evaluate all detectors on the fresh test simulation using the Java engine's alerts, with cluster-bootstrap confidence intervals, precision-recall curves, recall by difficulty, alert volume, and a generated RESULTS.md with charts.

**Architecture:** `eval/bootstrap.py` resamples manipulation windows by episode and normal windows by account; `eval/curves.py` holds PR curves and alert volume; `eval/freeze.py` hashes the frozen artifacts; `eval/final.py` runs the Java engine on a simulation directory, turns its alerts into per-window flags, scores the Isolation Forest in Python and computes everything into one results dict; `eval/charts.py` and `eval/results_md.py` render it. A rehearsal on seed 200 debugs the pipeline before the single run on seed 100.

**Tech Stack:** Python 3.12, numpy, pandas, scikit-learn, matplotlib, lightgbm, pytest; the existing Java engine.

Spec: `docs/superpowers/specs/2026-09-24-sentinel-evaluation-design.md`

---

## File Structure

```
src/sentinel/detect/dataset.py       (modify: load_events with the test-set guard)
src/sentinel/detect/model.py         (modify: IsolationForestDetector.load)
scripts/train_detectors.py           (modify: persist the Isolation Forest threshold)
src/sentinel/eval/bootstrap.py       cluster bootstrap, paired differences, recall by difficulty
src/sentinel/eval/curves.py          pr_curve, average_precision, operating_point, alert_volume
src/sentinel/eval/freeze.py          artifact hashing and verification
src/sentinel/eval/final.py           Java run, flags, evaluate, run_final_evaluation
src/sentinel/eval/charts.py          three figures
src/sentinel/eval/results_md.py      RESULTS.md renderer
scripts/freeze_artifacts.py
scripts/final_evaluation.py
docs/bench/stream_bench_v1.json      (copied Stage 3 benchmark results)
docs/bench/stream_bench_v2.json
tests/test_bootstrap.py  tests/test_curves.py  tests/test_freeze.py  tests/test_final.py
tests/test_charts.py     tests/test_results_md.py
tests/test_model.py      (modify)  tests/test_dataset.py (modify)
```

---

### Task 0: Persist the Isolation Forest threshold, guarded event loading

**Files:** Modify `src/sentinel/detect/dataset.py`, `src/sentinel/detect/model.py`, `scripts/train_detectors.py`, `tests/test_model.py`, `tests/test_dataset.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_model.py`:
```python
def test_isolation_forest_save_and_load_round_trip(synthetic_windows, tmp_path) -> None:
    train = synthetic_windows.iloc[:1600]
    detector = IsolationForestDetector(seed=1).fit(to_xy(train)[0])
    detector.save(tmp_path / "iforest.joblib")
    loaded = IsolationForestDetector.load(tmp_path / "iforest.joblib")
    X = to_xy(synthetic_windows.iloc[1600:])[0]
    assert np.allclose(detector.score(X), loaded.score(X))
```
Append to `tests/test_dataset.py`:
```python
def test_load_events_uses_the_same_test_set_guard(tmp_path, monkeypatch) -> None:
    from sentinel.detect.dataset import load_events

    cfg = SimConfig(instruments=("A",), events_per_instrument=3_000, episodes_per_instrument=3, seed=4)
    write_dataset(simulate(cfg), tmp_path / "test_seed4", cfg.window_s)
    write_dataset(simulate(cfg), tmp_path / "rehearsal_seed4", cfg.window_s)
    monkeypatch.setattr(dataset, "ACCESS_LOG", tmp_path / "test_access.log")
    with pytest.raises(PermissionError):
        load_events(tmp_path / "test_seed4")
    assert len(load_events(tmp_path / "rehearsal_seed4")) > 0 and not (tmp_path / "test_access.log").exists()
    assert len(load_events(tmp_path / "test_seed4", final=True)) > 0
    assert "test_seed4" in (tmp_path / "test_access.log").read_text()
```
Run: `python -m pytest tests/test_model.py tests/test_dataset.py -q`. Expected: the two new tests FAIL (`AttributeError ... 'load'` and `ImportError: cannot import name 'load_events'`).

- [ ] **Step 2: Implement**

In `src/sentinel/detect/model.py`, add to `IsolationForestDetector` (after `save`):
```python
    @classmethod
    def load(cls, path: Path) -> "IsolationForestDetector":
        """Inverse of ``save``."""
        detector = cls()
        detector._forest = joblib.load(path)
        return detector
```
In `src/sentinel/detect/dataset.py`, replace the guard block inside `load_sim` with a shared helper and add `load_events`:
```python
def _guard(sim_dir: Path, final: bool) -> None:
    """test_* directories need final=True and every load is logged."""
    if sim_dir.name.startswith("test_"):
        if not final:
            raise PermissionError(f"{sim_dir.name} is the final test set; pass final=True (logged) to load it")
        _log_access(sim_dir)


def load_events(sim_dir: Path, final: bool = False) -> pd.DataFrame:
    """Raw event log of a simulation directory, behind the same guard as ``load_sim``."""
    sim_dir = Path(sim_dir)
    _guard(sim_dir, final)
    return pd.read_parquet(sim_dir / "events.parquet")
```
and in `load_sim` replace the `if sim_dir.name.startswith("test_"): ...` block with `_guard(sim_dir, final)`.
In `scripts/train_detectors.py`: add `import json` at the top and, right after `forest.save(Path("models") / "iforest.joblib")`, add:
```python
    (Path("models") / "iforest_threshold.json").write_text(json.dumps({"threshold": iso_threshold}), encoding="utf-8")
```

- [ ] **Step 3: Run tests, retrain, verify determinism**

Run `python -m pytest -q` (all pass), then `python scripts/train_detectors.py`. The report's LightGBM rows must equal the last report (SPOOFING F1 0.407, LAYERING 0.619, WASH 0.318, ANY 0.452) and the Isolation Forest ANY row (F1 0.11). If anything differs, STOP and report. Confirm `models/iforest_threshold.json` exists.

- [ ] **Step 4: Commit**

```bash
git add src tests scripts
git commit -m "$(cat <<'EOF'
feat: persist Isolation Forest threshold; guarded load_events

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1: Cluster bootstrap

**Concept to explain:** a confidence interval says how much a metric could move if we drew a different sample of the same world. A naive bootstrap resamples windows one by one, but windows of the same spoofing episode are near-copies, so that would wildly overstate our certainty. Instead we resample whole *episodes* for the positives (an episode is either in the sample or not), and whole *accounts* for the innocent windows (a market maker's thousand quiet windows come and go together). Each replicate recomputes the metric; the middle 95% of replicates is the interval.

**Files:** Create `src/sentinel/eval/bootstrap.py`; Test `tests/test_bootstrap.py`

- [ ] **Step 1: Write the failing tests** (`tests/test_bootstrap.py`)

```python
"""Tests for the cluster bootstrap."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sentinel.eval.bootstrap import (METRICS, bootstrap_detectors, bootstrap_recall_by_difficulty,
                                     build_clusters, interval, paired_difference)
from sentinel.eval.metrics import type_metrics


def _table(n_episodes: int = 20, windows_per_episode: int = 5, n_accounts: int = 30, normal_per_account: int = 10,
           kind: str = "WASH") -> pd.DataFrame:
    rows = []
    for e in range(n_episodes):
        level = ("easy", "medium", "hard")[e % 3]
        for w in range(windows_per_episode):
            rows.append({"label": kind, "difficulty": level, "episode_id": e, "trader_id": 1000 + e,
                         "instrument": "A", "window": w})
    for a in range(n_accounts):
        for w in range(normal_per_account):
            rows.append({"label": "NORMAL", "difficulty": "none", "episode_id": -1, "trader_id": a,
                         "instrument": "A" if a % 2 else "B", "window": w})
    return pd.DataFrame(rows)


def test_build_clusters_counts_episodes_and_accounts() -> None:
    df = _table()
    c = build_clusters(df, "WASH")
    assert c.n_ep == 20 and c.n_acct == 30 and c.keep.all()
    other = pd.concat([df, df.iloc[:5].assign(label="SPOOFING", episode_id=99)], ignore_index=True)
    assert build_clusters(other, "WASH").keep.sum() == len(df)  # other types are excluded
    assert build_clusters(other, "ANY").n_ep == 21


def test_point_estimates_equal_the_plain_metrics() -> None:
    df = _table()
    rng = np.random.default_rng(0)
    flagged = rng.random(len(df)) < 0.3
    boot = bootstrap_detectors(df, {"d": flagged}, "WASH", n_boot=50, seed=1)
    plain = type_metrics(df["label"], flagged, "WASH")
    for metric in ("precision", "recall", "f1", "fpr"):
        assert boot.point["d"][metric] == pytest.approx(plain[metric])


def test_perfect_and_silent_detectors_have_degenerate_intervals() -> None:
    df = _table()
    perfect, silent = (df["label"] == "WASH").to_numpy(), np.zeros(len(df), dtype=bool)
    boot = bootstrap_detectors(df, {"perfect": perfect, "silent": silent}, "WASH", n_boot=200, seed=2)
    for metric in ("precision", "recall", "f1", "episode_recall"):
        assert interval(boot.reps["perfect"][metric]) == (1.0, 1.0)
    assert interval(boot.reps["perfect"]["fpr"]) == (0.0, 0.0)
    assert interval(boot.reps["silent"]["recall"]) == (0.0, 0.0) and boot.point["silent"]["precision"] == 0.0


def test_resampling_by_episode_widens_the_interval_versus_windows() -> None:
    """Two 10-window episodes, one fully caught, one fully missed: recall is 0, 0.5 or 1 per replicate."""
    df = _table(n_episodes=2, windows_per_episode=10, n_accounts=5)
    flagged = ((df["label"] == "WASH") & (df["episode_id"] == 0)).to_numpy()
    boot = bootstrap_detectors(df, {"d": flagged}, "WASH", n_boot=500, seed=3)
    lo, hi = interval(boot.reps["d"]["recall"])
    assert (lo, hi) == (0.0, 1.0) and boot.point["d"]["recall"] == 0.5


def test_intervals_cover_the_true_episode_recall_at_about_the_nominal_rate() -> None:
    rng = np.random.default_rng(7)
    hits = 0
    worlds = 150
    for w in range(worlds):
        df = _table(n_episodes=80, windows_per_episode=3, n_accounts=20, normal_per_account=5)
        caught = rng.random(80) < 0.6  # true episode-level recall is 0.6
        flagged = np.where(df["label"] == "WASH", caught[df["episode_id"].clip(lower=0).to_numpy()], False)
        boot = bootstrap_detectors(df, {"d": flagged}, "WASH", n_boot=300, seed=w)
        lo, hi = interval(boot.reps["d"]["episode_recall"])
        hits += lo <= 0.6 <= hi
    assert 0.88 <= hits / worlds <= 1.0


def test_paired_difference_is_zero_for_identical_detectors_and_positive_for_a_better_one() -> None:
    df = _table()
    truth = (df["label"] == "WASH").to_numpy()
    rng = np.random.default_rng(1)
    noisy = truth & (rng.random(len(df)) < 0.5)
    boot = bootstrap_detectors(df, {"a": truth, "b": truth.copy(), "c": noisy}, "WASH", n_boot=300, seed=4)
    assert paired_difference(boot, "a", "b", "f1") == (0.0, 0.0, 0.0)
    diff, lo, hi = paired_difference(boot, "a", "c", "recall")
    assert diff > 0 and lo > 0


def test_recall_by_difficulty_uses_episodes_within_each_level() -> None:
    df = _table(n_episodes=30)
    flagged = (df["label"] == "WASH").to_numpy() & (df["difficulty"] == "easy").to_numpy()
    out = bootstrap_recall_by_difficulty(df, {"d": flagged}, "WASH", n_boot=200, seed=5)["d"]
    assert out["easy"]["point"] == 1.0 and out["medium"]["point"] == 0.0 and out["hard"]["point"] == 0.0
    assert out["easy"]["n"] == 50 and out["easy"]["n_episodes"] == 10
    assert out["hard"]["lo"] == 0.0 and out["hard"]["hi"] == 0.0


def test_no_positives_raises() -> None:
    with pytest.raises(ValueError):
        build_clusters(_table(kind="SPOOFING"), "WASH")
    assert set(METRICS) == {"precision", "recall", "f1", "fpr", "episode_recall"}
```
Run: `python -m pytest tests/test_bootstrap.py -v`. Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.eval.bootstrap'`.

- [ ] **Step 2: Write `src/sentinel/eval/bootstrap.py`**

```python
"""Cluster-bootstrap confidence intervals for detection metrics.

Windows of one manipulation episode are near-copies, and an account's innocent
windows share behaviour (a market maker's are all alike), so resampling single
windows would overstate certainty. We resample whole clusters: manipulation
windows by episode, normal windows by account (trader within instrument).
Each replicate draws cluster multiplicities from a multinomial and recomputes
the metrics from per-cluster counts, so thousands of replicates are cheap.
The same replicates are shared by all detectors, which makes paired
differences valid.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

METRICS = ("precision", "recall", "f1", "fpr", "episode_recall")
LEVELS = ("easy", "medium", "hard")


@dataclass
class Clusters:
    """Cluster structure of one evaluation (one type, or ANY) over a window table."""

    keep: np.ndarray  # bool per window: part of this evaluation
    is_pos: np.ndarray  # bool per kept window
    ep: np.ndarray  # episode cluster of each kept positive window (-1 for negatives)
    acct: np.ndarray  # account cluster of each kept negative window (-1 for positives)
    n_ep: int
    n_acct: int


@dataclass
class BootResult:
    """Point estimates on the real data and per-replicate metric arrays, per detector."""

    kind: str
    n_boot: int
    point: dict[str, dict[str, float]]
    reps: dict[str, dict[str, np.ndarray]]


def build_clusters(df: pd.DataFrame, kind: str) -> Clusters:
    """Cluster structure for ``kind`` (a type or 'ANY'). Positives: that type; negatives: all NORMAL windows."""
    label = df["label"].to_numpy()
    pos = (label != "NORMAL") if kind == "ANY" else (label == kind)
    neg = label == "NORMAL"
    keep = pos | neg
    is_pos = pos[keep]
    ep = np.full(keep.sum(), -1, dtype=np.int64)
    acct = np.full(keep.sum(), -1, dtype=np.int64)
    episode_ids = df["episode_id"].to_numpy()[keep]
    if is_pos.any():
        ep[is_pos] = pd.factorize(episode_ids[is_pos])[0]
    accounts = (df["instrument"].astype(str) + "|" + df["trader_id"].astype(str)).to_numpy()[keep]
    if (~is_pos).any():
        acct[~is_pos] = pd.factorize(accounts[~is_pos])[0]
    n_ep, n_acct = int(ep.max() + 1) if is_pos.any() else 0, int(acct.max() + 1) if (~is_pos).any() else 0
    if n_ep == 0 or n_acct == 0:
        raise ValueError(f"need both positive and normal windows for {kind}")
    return Clusters(keep, is_pos, ep, acct, n_ep, n_acct)


def _div(a, b):
    """a / b, with 0.0 where b is 0. Works on scalars and arrays."""
    b = np.asarray(b, dtype=float)
    return np.where(b > 0, np.asarray(a, dtype=float) / np.where(b > 0, b, 1.0), 0.0)


def _metrics(tp, pos, fp, neg, detected, n_episodes) -> dict:
    """Metrics from (weighted) totals: works on scalars and on arrays of replicates."""
    precision = _div(tp, tp + fp)
    recall = _div(tp, pos)
    f1 = _div(2 * tp, 2 * tp + fp + (pos - tp))
    return {"precision": precision, "recall": recall, "f1": f1, "fpr": _div(fp, neg),
            "episode_recall": _div(detected, n_episodes)}


def _cluster_counts(c: Clusters, flagged: np.ndarray):
    f = np.asarray(flagged, dtype=float)[c.keep]
    pos, neg = c.is_pos, ~c.is_pos
    tp_e = np.bincount(c.ep[pos], weights=f[pos], minlength=c.n_ep)
    n_e = np.bincount(c.ep[pos], minlength=c.n_ep).astype(float)
    fp_a = np.bincount(c.acct[neg], weights=f[neg], minlength=c.n_acct)
    n_a = np.bincount(c.acct[neg], minlength=c.n_acct).astype(float)
    return tp_e, n_e, (tp_e > 0).astype(float), fp_a, n_a


def bootstrap_detectors(df: pd.DataFrame, flags: Mapping[str, np.ndarray], kind: str,
                        n_boot: int = 2000, seed: int = 0) -> BootResult:
    """Point estimates and bootstrap replicates of every metric for each detector's boolean flags."""
    c = build_clusters(df, kind)
    rng = np.random.default_rng(seed)
    m_e = rng.multinomial(c.n_ep, np.full(c.n_ep, 1.0 / c.n_ep), size=n_boot).astype(float)
    m_a = rng.multinomial(c.n_acct, np.full(c.n_acct, 1.0 / c.n_acct), size=n_boot).astype(float)
    point, reps = {}, {}
    for name, flagged in flags.items():
        tp_e, n_e, d_e, fp_a, n_a = _cluster_counts(c, flagged)
        point[name] = {k: float(v) for k, v in
                       _metrics(tp_e.sum(), n_e.sum(), fp_a.sum(), n_a.sum(), d_e.sum(), c.n_ep).items()}
        reps[name] = _metrics(m_e @ tp_e, m_e @ n_e, m_a @ fp_a, m_a @ n_a, m_e @ d_e, c.n_ep)
    return BootResult(kind, n_boot, point, reps)


def interval(replicates: np.ndarray, alpha: float = 0.05) -> tuple[float, float]:
    """Percentile interval (default 95%) of a replicate array."""
    lo, hi = np.percentile(replicates, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def paired_difference(boot: BootResult, a: str, b: str, metric: str, alpha: float = 0.05) -> tuple[float, float, float]:
    """(point difference a - b, lower, upper) using the shared replicates."""
    diff = boot.point[a][metric] - boot.point[b][metric]
    lo, hi = interval(boot.reps[a][metric] - boot.reps[b][metric], alpha)
    return float(diff), lo, hi


def bootstrap_recall_by_difficulty(df: pd.DataFrame, flags: Mapping[str, np.ndarray], kind: str,
                                   n_boot: int = 2000, seed: int = 0) -> dict[str, dict[str, dict[str, float]]]:
    """Window-level recall (with interval) of each detector within each difficulty level, by episode resampling."""
    label = df["label"].to_numpy()
    positive = (label != "NORMAL") if kind == "ANY" else (label == kind)
    rng = np.random.default_rng(seed)
    out: dict[str, dict[str, dict[str, float]]] = {name: {} for name in flags}
    for level in LEVELS:
        mask = positive & (df["difficulty"].to_numpy() == level)
        if not mask.any():
            continue
        ep = pd.factorize(df["episode_id"].to_numpy()[mask])[0]
        n_ep = int(ep.max() + 1)
        m = rng.multinomial(n_ep, np.full(n_ep, 1.0 / n_ep), size=n_boot).astype(float)
        n_e = np.bincount(ep, minlength=n_ep).astype(float)
        for name, flagged in flags.items():
            tp_e = np.bincount(ep, weights=np.asarray(flagged, dtype=float)[mask], minlength=n_ep)
            lo, hi = interval(_div(m @ tp_e, m @ n_e))
            out[name][level] = {"n": int(mask.sum()), "n_episodes": n_ep, "point": float(tp_e.sum() / n_e.sum()),
                                "lo": lo, "hi": hi}
    return out
```

- [ ] **Step 3: Run tests and commit**

Run: `python -m pytest tests/test_bootstrap.py -v` then `python -m pytest -q`. Expected: all pass. If the coverage test fails (outside 0.88-1.0), print the observed rate before deciding: an observed rate well below nominal means the resampling is wrong (check that positives are resampled by episode, not by window); do not widen the acceptance band.
```bash
git add src/sentinel/eval/bootstrap.py tests/test_bootstrap.py
git commit -m "$(cat <<'EOF'
feat(eval): cluster bootstrap (episodes and accounts), paired differences

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: PR curves, average precision, alert volume

**Concept to explain:** a precision-recall curve shows the trade-off: raise the alert threshold and precision rises (fewer false alerts) but recall falls (more abuse missed). Average precision (AP) is the area under it, a single number that doesn't depend on a threshold. Alert volume turns FPR into analyst workload: "false alerts per 1,000 innocent windows" and "per instrument-hour".

**Files:** Create `src/sentinel/eval/curves.py`; Test `tests/test_curves.py`

- [ ] **Step 1: Write the failing tests** (`tests/test_curves.py`)

```python
"""Tests for PR curves, operating points and alert volume."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sentinel.eval.curves import alert_volume, average_precision, operating_point, pr_curve


def _labels() -> pd.Series:
    return pd.Series(["NORMAL"] * 6 + ["WASH"] * 2 + ["SPOOFING"] * 2)


def test_perfect_scores_give_a_perfect_curve_and_ap() -> None:
    scores = np.array([0.1] * 6 + [0.9] * 2 + [0.5] * 2)
    curve = pr_curve(_labels(), scores, "WASH")
    assert 1.0 in curve["precision"] and curve["recall"][0] == 1.0 or max(curve["recall"]) == 1.0
    assert average_precision(_labels(), scores, "WASH") == pytest.approx(1.0)


def test_other_types_are_excluded_from_the_curve() -> None:
    scores = np.array([0.1] * 6 + [0.9] * 2 + [0.99] * 2)  # the SPOOFING rows score highest but must be ignored
    assert average_precision(_labels(), scores, "WASH") == pytest.approx(1.0)


def test_any_treats_every_type_as_positive() -> None:
    scores = np.array([0.1] * 6 + [0.9] * 4)
    assert average_precision(_labels(), scores, "ANY") == pytest.approx(1.0)


def test_curve_is_downsampled_to_max_points_and_keeps_the_endpoints() -> None:
    rng = np.random.default_rng(0)
    labels = pd.Series(np.where(rng.random(5_000) < 0.05, "WASH", "NORMAL"))
    curve = pr_curve(labels, rng.random(5_000), "WASH", max_points=100)
    assert len(curve["precision"]) == len(curve["recall"]) <= 100
    assert curve["recall"][-1] == 0.0 and curve["precision"][-1] == 1.0  # sklearn's final (recall 0, precision 1) point


def test_operating_point_is_precision_and_recall_of_the_flags() -> None:
    flagged = np.array([True] * 3 + [False] * 3 + [True, False, True, False])
    precision, recall = operating_point(_labels(), flagged, "WASH")
    assert precision == pytest.approx(1 / 4) and recall == pytest.approx(1 / 2)  # tp=1 (of 2), fp=3 of the normals


def test_alert_volume_converts_fpr_into_workload() -> None:
    labels = pd.Series(["NORMAL"] * 1000 + ["WASH"] * 10)
    flagged = np.array([True] * 20 + [False] * 980 + [True] * 5 + [False] * 5)
    v = alert_volume(labels, flagged, "WASH", n_instruments=4, sim_hours=2.0)
    assert v["false_alerts"] == 20 and v["true_alerts"] == 5
    assert v["false_alerts_per_1000_innocent_windows"] == pytest.approx(20.0)
    assert v["false_alerts_per_instrument_hour"] == pytest.approx(20 / 8)
```
Run: `python -m pytest tests/test_curves.py -v`. Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 2: Write `src/sentinel/eval/curves.py`**

```python
"""Precision-recall curves, average precision and alert volume."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, precision_recall_curve

from sentinel.eval.metrics import any_metrics, type_metrics


def _subset(labels: pd.Series, kind: str) -> tuple[np.ndarray, np.ndarray]:
    """(mask of windows in this evaluation, boolean positives among them)."""
    if kind == "ANY":
        keep = np.ones(len(labels), dtype=bool)
        return keep, (labels != "NORMAL").to_numpy()
    keep = labels.isin([kind, "NORMAL"]).to_numpy()
    return keep, (labels.to_numpy() == kind)[keep]


def pr_curve(labels: pd.Series, scores: np.ndarray, kind: str, max_points: int = 300) -> dict[str, list[float]]:
    """Precision-recall curve of a score for one type (or ANY), thinned to at most ``max_points`` points."""
    keep, y = _subset(labels, kind)
    precision, recall, _ = precision_recall_curve(y, np.asarray(scores)[keep])
    if len(precision) > max_points:
        take = np.unique(np.linspace(0, len(precision) - 1, max_points).astype(int))
        precision, recall = precision[take], recall[take]
    return {"precision": [float(p) for p in precision], "recall": [float(r) for r in recall]}


def average_precision(labels: pd.Series, scores: np.ndarray, kind: str) -> float:
    """Average precision (area under the precision-recall curve) for one type (or ANY)."""
    keep, y = _subset(labels, kind)
    return float(average_precision_score(y, np.asarray(scores)[keep]))


def operating_point(labels: pd.Series, flagged: np.ndarray, kind: str) -> tuple[float, float]:
    """(precision, recall) of a set of boolean flags for one type (or ANY)."""
    m = any_metrics(labels, flagged) if kind == "ANY" else type_metrics(labels, flagged, kind)
    return m["precision"], m["recall"]


def alert_volume(labels: pd.Series, flagged: np.ndarray, kind: str, n_instruments: int,
                 sim_hours: float) -> dict[str, float]:
    """Analyst workload implied by a detector's flags."""
    m = any_metrics(labels, flagged) if kind == "ANY" else type_metrics(labels, flagged, kind)
    return {"false_alerts": m["fp"], "true_alerts": m["tp"],
            "false_alerts_per_1000_innocent_windows": 1000.0 * m["fpr"],
            "false_alerts_per_instrument_hour": m["fp"] / (n_instruments * sim_hours)}
```

- [ ] **Step 3: Run tests and commit**

Run: `python -m pytest tests/test_curves.py -v` then `python -m pytest -q`. Expected: all pass. (The first test's compound `assert` is deliberately tolerant of sklearn's point ordering; if it fails for a genuine reason, print `curve` and fix the test's expectation by hand-checking the curve, not the code.)
```bash
git add src/sentinel/eval/curves.py tests/test_curves.py
git commit -m "$(cat <<'EOF'
feat(eval): PR curves, average precision, operating points, alert volume

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Freeze manifest

**Concept to explain:** to prove the test set did not influence any modeling decision, every file that determines predictions is hashed (sha-256) *before* the test run and the hashes are committed and tagged. The final script re-hashes and refuses to run if anything changed. Line endings are normalised before hashing so a Windows/Linux checkout doesn't change the hash.

**Files:** Create `src/sentinel/eval/freeze.py`, `scripts/freeze_artifacts.py`; Test `tests/test_freeze.py`

- [ ] **Step 1: Write the failing tests** (`tests/test_freeze.py`)

```python
"""Tests for the freeze manifest."""
from __future__ import annotations

import json

from sentinel.eval.freeze import build_manifest, verify_manifest, write_manifest


def _tree(root):
    for rel, content in {"models/lgbm.txt": b"model\n", "models/thresholds.json": b"{}",
                         "models/tuned_rules.json": b"{}", "models/iforest.joblib": b"\x00\x01",
                         "models/iforest_threshold.json": b"{}", "src/sentinel/features/extractor.py": b"x = 1\n",
                         "src/sentinel/detect/rules.py": b"y = 2\n", "java/src/sentinel/A.java": b"class A {}\n"}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def test_manifest_lists_every_frozen_file_including_java_sources(tmp_path) -> None:
    _tree(tmp_path)
    manifest = build_manifest(tmp_path)
    assert "models/lgbm.txt" in manifest and "java/src/sentinel/A.java" in manifest
    assert all(len(h) == 64 for h in manifest.values())


def test_verify_passes_until_a_frozen_file_changes(tmp_path) -> None:
    _tree(tmp_path)
    write_manifest(tmp_path / "docs" / "frozen-artifacts.json", tmp_path)
    assert verify_manifest(tmp_path / "docs" / "frozen-artifacts.json", tmp_path) == []
    (tmp_path / "models" / "thresholds.json").write_bytes(b'{"WASH": 0.5}')
    problems = verify_manifest(tmp_path / "docs" / "frozen-artifacts.json", tmp_path)
    assert problems == ["changed: models/thresholds.json"]


def test_verify_reports_missing_and_new_java_files(tmp_path) -> None:
    _tree(tmp_path)
    write_manifest(tmp_path / "m.json", tmp_path)
    (tmp_path / "models" / "lgbm.txt").unlink()
    (tmp_path / "java" / "src" / "sentinel" / "B.java").write_bytes(b"class B {}\n")
    problems = verify_manifest(tmp_path / "m.json", tmp_path)
    assert "missing: models/lgbm.txt" in problems and "new: java/src/sentinel/B.java" in problems


def test_line_endings_do_not_change_the_hash(tmp_path) -> None:
    _tree(tmp_path)
    before = build_manifest(tmp_path)["src/sentinel/detect/rules.py"]
    (tmp_path / "src/sentinel/detect/rules.py").write_bytes(b"y = 2\r\n")
    assert build_manifest(tmp_path)["src/sentinel/detect/rules.py"] == before
    assert json.loads(json.dumps(build_manifest(tmp_path)))  # serialisable
```
Run: `python -m pytest tests/test_freeze.py -v`. Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 2: Write `src/sentinel/eval/freeze.py`**

```python
"""Hash the artifacts that determine predictions, so the test run can prove nothing changed."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MODEL_FILES = ("models/lgbm.txt", "models/thresholds.json", "models/tuned_rules.json",
               "models/iforest.joblib", "models/iforest_threshold.json")
CODE_FILES = ("src/sentinel/features/extractor.py", "src/sentinel/detect/rules.py")


def _digest(path: Path) -> str:
    """sha-256 of the file with CRLF normalised to LF (so checkouts on different OSes agree)."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _java_sources(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in (root / "java" / "src").rglob("*.java"))


def build_manifest(root: Path = Path(".")) -> dict[str, str]:
    """{relative path: sha-256} for the model files, extractor, rules and every Java source."""
    root = Path(root)
    return {rel: _digest(root / rel) for rel in (*MODEL_FILES, *CODE_FILES, *_java_sources(root))}


def write_manifest(path: Path, root: Path = Path(".")) -> dict[str, str]:
    """Write the manifest as JSON and return it."""
    manifest = build_manifest(root)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def verify_manifest(path: Path, root: Path = Path(".")) -> list[str]:
    """Problems found comparing the tree against a manifest ('missing', 'changed' or 'new' lines). Empty means frozen."""
    root = Path(root)
    expected = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = []
    for rel, digest in expected.items():
        file = root / rel
        if not file.exists():
            problems.append(f"missing: {rel}")
        elif _digest(file) != digest:
            problems.append(f"changed: {rel}")
    problems += [f"new: {rel}" for rel in _java_sources(root) if rel not in expected]
    return problems
```

- [ ] **Step 3: Write `scripts/freeze_artifacts.py`**

```python
"""Write docs/frozen-artifacts.json (run once, then commit and tag `stage4-frozen`).

python scripts/freeze_artifacts.py [--out docs/frozen-artifacts.json]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from sentinel.eval.freeze import write_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("docs/frozen-artifacts.json"))
    args = parser.parse_args()
    manifest = write_manifest(args.out)
    for rel, digest in manifest.items():
        print(f"{digest[:16]}  {rel}")
    print(f"wrote {args.out} ({len(manifest)} files)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/test_freeze.py -v` then `python -m pytest -q`. Expected: all pass. Do NOT run `freeze_artifacts.py` yet (the freeze happens in Task 6, after the rehearsal).
```bash
git add src/sentinel/eval/freeze.py scripts/freeze_artifacts.py tests/test_freeze.py
git commit -m "$(cat <<'EOF'
feat(eval): freeze manifest for model, extractor, rules and Java sources

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Evaluation orchestration and the rehearsal run

**Concept to explain:** the pipeline is: events, then the Java engine (alerts and probabilities), then flags per window per detector, then metrics with bootstrap intervals. The Java alerts are matched to labeled windows by (trader, instrument, window); an alert for a window that isn't in the label table would be a bug, so it raises.

**Files:** Create `src/sentinel/eval/final.py`, `scripts/final_evaluation.py`, `tests/test_final.py`; create `docs/bench/`

- [ ] **Step 1: Write the failing tests** (`tests/test_final.py`)

```python
"""Tests for turning engine alerts into flags and for the evaluation dict."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sentinel.eval.final import ALL_KINDS, DETECTORS, engine_flags, evaluate


def synthetic_eval_inputs(n_episodes: int = 24, seed: int = 0):
    """A labeled window table, engine-style flags and scores for every detector."""
    rng = np.random.default_rng(seed)
    rows = []
    for e in range(n_episodes):
        kind = ("SPOOFING", "LAYERING", "WASH")[e % 3]
        level = ("easy", "medium", "hard")[(e // 3) % 3]
        for w in range(4):
            rows.append({"label": kind, "difficulty": level, "episode_id": e, "trader_id": 500 + e,
                         "instrument": "A", "window": w})
    for a in range(40):
        for w in range(6):
            rows.append({"label": "NORMAL", "difficulty": "none", "episode_id": -1, "trader_id": a,
                         "instrument": ("A", "B")[a % 2], "window": w})
    df = pd.DataFrame(rows)
    pos = (df["label"] != "NORMAL").to_numpy()
    flags, scores = {}, {}
    for i, det in enumerate(DETECTORS):
        strength = 0.3 + 0.15 * i
        base = np.where(pos, rng.random(len(df)) < strength, rng.random(len(df)) < 0.05)
        flags[det] = {k: (base & (df["label"] == k).to_numpy()) | (base & ~pos) if k != "ANY" else base
                      for k in ALL_KINDS}
    for det in ("LightGBM", "Isolation Forest"):
        scores[det] = {k: rng.random(len(df)) + pos * 0.5 for k in ALL_KINDS}
    return df, flags, scores


def test_engine_flags_maps_alerts_to_windows_by_detector_and_kind() -> None:
    df = pd.DataFrame({"trader_id": [1, 1, 2], "instrument": ["A", "A", "B"], "window": [0, 1, 0]})
    alerts = pd.DataFrame({"detector": ["model", "generic", "tuned", "textbook"],
                           "kind": ["WASH", "ANY", "SPOOFING", "LAYERING"],
                           "trader_id": [1, 1, 2, 2], "instrument": ["A", "A", "B", "B"], "window": [0, 1, 0, 0]})
    flags = engine_flags(df, alerts)
    assert list(flags["LightGBM"]["WASH"]) == [True, False, False] and list(flags["LightGBM"]["ANY"]) == [True, False, False]
    assert all(list(flags["generic rule"][k]) == [False, True, False] for k in ALL_KINDS)  # untyped: applies to every type
    assert list(flags["tuned rules"]["SPOOFING"]) == [False, False, True]
    assert list(flags["textbook rules"]["LAYERING"]) == [False, False, True]
    assert list(flags["textbook rules"]["ANY"]) == [False, False, True]


def test_engine_flags_rejects_alerts_for_unknown_windows() -> None:
    df = pd.DataFrame({"trader_id": [1], "instrument": ["A"], "window": [0]})
    alerts = pd.DataFrame({"detector": ["model"], "kind": ["WASH"], "trader_id": [9], "instrument": ["A"], "window": [0]})
    with pytest.raises(ValueError):
        engine_flags(df, alerts)


def test_evaluate_produces_the_full_results_structure() -> None:
    df, flags, scores = synthetic_eval_inputs()
    result = evaluate(df, flags, scores, n_instruments=2, sim_hours=1.0, n_boot=100, seed=1)
    assert result["detectors"] == list(DETECTORS)
    for kind in ALL_KINDS:
        cell = result["metrics"][kind]["LightGBM"]["f1"]
        assert cell["lo"] <= cell["point"] <= cell["hi"] or cell["lo"] <= cell["hi"]
        assert set(result["metrics"][kind]) == set(DETECTORS)
        assert {"curves", "ap", "points", "operating"} <= set(result["pr"][kind])
        assert set(result["pr"][kind]["curves"]) == {"LightGBM", "Isolation Forest"}
        assert result["alert_volume"][kind]["LightGBM"]["false_alerts"] >= 0
        assert {"a", "b", "metric", "diff", "lo", "hi"} <= set(result["paired"][kind][0])
    assert set(result["recall_by_difficulty"]["WASH"]["LightGBM"]) == {"easy", "medium", "hard"}
    assert result["metrics"]["WASH"]["LightGBM"]["episode_recall"]["point"] >= 0
```
Run: `python -m pytest tests/test_final.py -v`. Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.eval.final'`.

- [ ] **Step 2: Write `src/sentinel/eval/final.py`**

```python
"""Final evaluation: Java engine alerts (and Python Isolation Forest) scored with bootstrap intervals."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from sentinel.detect import dataset
from sentinel.detect.dataset import load_events, load_sim
from sentinel.detect.model import IsolationForestDetector
from sentinel.eval import bootstrap as bs
from sentinel.eval.curves import alert_volume, average_precision, operating_point, pr_curve
from sentinel.eval.freeze import verify_manifest
from sentinel.features.extractor import FEATURE_NAMES
from sentinel.stream import java as jv
from sentinel.stream.engine import load_engine_parts
from sentinel.stream.export import write_engine_config, write_events

DETECTORS = ("generic rule", "textbook rules", "tuned rules", "LightGBM", "Isolation Forest")
KINDS = ("SPOOFING", "LAYERING", "WASH")
ALL_KINDS = (*KINDS, "ANY")
JAVA_NAME = {"generic rule": "generic", "textbook rules": "textbook", "tuned rules": "tuned", "LightGBM": "model"}
RULE_POINTS = ("generic rule", "textbook rules", "tuned rules")
PAIRED = (("LightGBM", "tuned rules"), ("LightGBM", "textbook rules"), ("LightGBM", "Isolation Forest"))
PAIRED_METRICS = ("f1", "recall", "fpr")
KEY = ["trader_id", "instrument", "window"]


def run_java(events: pd.DataFrame, model_dir: Path, workdir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the Java engine on an event log. Returns (feature rows with p0..p3, alerts), instruments as names."""
    booster, thresholds, rule_sets = load_engine_parts(model_dir)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    names = write_events(events, workdir / "events.bin")
    write_engine_config(workdir / "engine.properties", window_s=10, instruments=names,
                        thresholds=thresholds, rule_sets=rule_sets)
    booster.save_model(str(workdir / "lgbm.txt"))
    jv.build()
    jv.run_engine(workdir / "events.bin", workdir / "engine.properties", workdir / "lgbm.txt", workdir / "java_out")
    feats = pd.read_csv(workdir / "java_out" / "features.csv", float_precision="round_trip")
    alerts = pd.read_csv(workdir / "java_out" / "alerts.csv", float_precision="round_trip")
    for frame in (feats, alerts):
        frame["instrument"] = frame["instrument"].map(dict(enumerate(names)))
    return (feats.rename(columns={"trader": "trader_id"}), alerts.rename(columns={"trader": "trader_id"}))


def engine_flags(df: pd.DataFrame, alerts: pd.DataFrame) -> dict[str, dict[str, np.ndarray]]:
    """Boolean flag per window (rows of ``df``) for each engine detector and type, including 'ANY'.

    The generic rule is untyped, so its flag applies to every type.
    """
    flags = {d: {k: np.zeros(len(df), dtype=bool) for k in ALL_KINDS} for d in JAVA_NAME}
    if len(alerts):
        index = pd.MultiIndex.from_frame(df[KEY])
        pos = index.get_indexer(pd.MultiIndex.from_frame(alerts[KEY]))
        if (pos < 0).any():
            raise ValueError("engine produced an alert for a window that is not in the label table")
        detector, kind = alerts["detector"].to_numpy(), alerts["kind"].to_numpy()
        for name, java in JAVA_NAME.items():
            rows = detector == java
            for k in KINDS:
                flags[name][k][pos[rows & (kind == k)]] = True
            flags[name]["ANY"][pos[rows]] = True
    for k in KINDS:
        flags["generic rule"][k] = flags["generic rule"]["ANY"].copy()
    return flags


def _cell(point: float, lo: float, hi: float) -> dict[str, float]:
    return {"point": float(point), "lo": float(lo), "hi": float(hi)}


def evaluate(df: pd.DataFrame, flags: Mapping[str, Mapping[str, np.ndarray]],
             scores: Mapping[str, Mapping[str, np.ndarray]], n_instruments: int, sim_hours: float,
             n_boot: int = 2000, seed: int = 0) -> dict:
    """All metrics, intervals, paired differences, recall by difficulty, alert volume and PR data."""
    result: dict = {"detectors": list(DETECTORS), "metrics": {}, "recall_by_difficulty": {}, "paired": {},
                    "alert_volume": {}, "pr": {}}
    for kind in ALL_KINDS:
        by_det = {d: flags[d][kind] for d in DETECTORS}
        boot = bs.bootstrap_detectors(df, by_det, kind, n_boot, seed)
        result["metrics"][kind] = {
            d: {m: _cell(boot.point[d][m], *bs.interval(boot.reps[d][m])) for m in bs.METRICS} for d in DETECTORS}
        result["paired"][kind] = [
            {"a": a, "b": b, "metric": m, **dict(zip(("diff", "lo", "hi"), bs.paired_difference(boot, a, b, m)))}
            for a, b in PAIRED for m in PAIRED_METRICS]
        result["recall_by_difficulty"][kind] = bs.bootstrap_recall_by_difficulty(df, by_det, kind, n_boot, seed)
        result["alert_volume"][kind] = {d: alert_volume(df["label"], by_det[d], kind, n_instruments, sim_hours)
                                        for d in DETECTORS}
        curves = {d: pr_curve(df["label"], scores[d][kind], kind) for d in ("LightGBM", "Isolation Forest")}
        result["pr"][kind] = {
            "curves": curves,
            "ap": {d: average_precision(df["label"], scores[d][kind], kind) for d in curves},
            "points": {d: list(operating_point(df["label"], by_det[d], kind)) for d in RULE_POINTS},
            "operating": {d: list(operating_point(df["label"], by_det[d], kind))
                          for d in ("LightGBM", "Isolation Forest")}}
    return result


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run_final_evaluation(sim_dir: Path, model_dir: Path = Path("models"), work_dir: Path = Path("artifacts/final_work"),
                         final: bool = False, manifest: Path | None = Path("docs/frozen-artifacts.json"),
                         n_boot: int = 2000, seed: int = 0) -> dict:
    """Evaluate every detector on a simulation directory and return the results dict.

    Test directories (``test_*``) require ``final=True`` and a manifest that matches the tree exactly.
    """
    sim_dir = Path(sim_dir)
    is_test = sim_dir.name.startswith("test_")
    if is_test:
        if manifest is None or not Path(manifest).exists():
            raise RuntimeError("the final test run needs the frozen-artifact manifest")
        problems = verify_manifest(manifest)
        if problems:
            raise RuntimeError("frozen artifacts changed since the freeze:\n" + "\n".join(problems))
    events = load_events(sim_dir, final=final)
    df = load_sim(sim_dir, final=final, rebuild=True) if not is_test else load_sim(sim_dir, final=final, rebuild=True)
    feats, alerts = run_java(events, model_dir, work_dir / sim_dir.name)

    probs = df[KEY].merge(feats[KEY + ["p0", "p1", "p2", "p3"]], on=KEY, how="left", validate="one_to_one")
    if probs[["p0", "p1", "p2", "p3"]].isna().any().any() or len(probs) != len(df):
        raise ValueError("Java feature rows do not match the label table")
    proba = probs[["p0", "p1", "p2", "p3"]].to_numpy()

    flags = engine_flags(df, alerts)
    forest = IsolationForestDetector.load(Path(model_dir) / "iforest.joblib")
    threshold = json.loads((Path(model_dir) / "iforest_threshold.json").read_text(encoding="utf-8"))["threshold"]
    iso_scores = forest.score(df[list(FEATURE_NAMES)].to_numpy(dtype=np.float64))
    flags["Isolation Forest"] = {k: iso_scores >= threshold for k in ALL_KINDS}
    class_index = {"SPOOFING": 1, "LAYERING": 2, "WASH": 3}
    scores = {"LightGBM": {**{k: proba[:, i] for k, i in class_index.items()}, "ANY": 1.0 - proba[:, 0]},
              "Isolation Forest": {k: iso_scores for k in ALL_KINDS}}

    n_instruments = int(events["instrument"].astype(str).nunique())
    sim_hours = float((events["ts"].max() + 1) / 1e9 / 3600)
    result = evaluate(df, flags, scores, n_instruments, sim_hours, n_boot, seed)
    label = df["label"]
    result["meta"] = {
        "sim_dir": sim_dir.name, "n_events": int(len(events)), "n_windows": int(len(df)),
        "n_manipulation_windows": int((label != "NORMAL").sum()),
        "n_episodes": int(df.loc[df["episode_id"] >= 0, "episode_id"].nunique()),
        "windows_by_type": {k: int((label == k).sum()) for k in KINDS}, "n_instruments": n_instruments,
        "sim_hours": sim_hours, "n_boot": n_boot, "seed": seed, "git_commit": _git_commit(),
        "date_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "final": bool(is_test),
        "n_java_alerts": int(len(alerts)), "n_rows": int(len(feats))}
    access = dataset.ACCESS_LOG
    result["meta"]["access_log"] = access.read_text(encoding="utf-8").splitlines() if access.exists() else []
    return result
```
(Note for the implementer: the line `df = load_sim(...) if not is_test else load_sim(...)` above is intentionally identical in both branches; simplify it to a single `load_sim(sim_dir, final=final, rebuild=True)` call.)

- [ ] **Step 3: Write `scripts/final_evaluation.py`**

```python
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
```

- [ ] **Step 4: Run tests, fix, commit the code**

Run: `python -m pytest tests/test_final.py -v` then `python -m pytest -q`. Expected: pass (simplify the duplicated `load_sim` line as noted). Commit:
```bash
git add src/sentinel/eval/final.py scripts/final_evaluation.py tests/test_final.py
git commit -m "$(cat <<'EOF'
feat(eval): evaluation orchestration over Java engine alerts

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Generate the rehearsal simulation and copy the Stage 3 benchmark results**

```bash
python scripts/generate_data.py --out data/rehearsal_seed200 --seed 200
mkdir -p docs/bench && cp artifacts/stream_bench_v1.json docs/bench/stream_bench_v1.json && cp artifacts/stream_bench_v2.json docs/bench/stream_bench_v2.json
```
(`data/` is gitignored; the two small JSON files are committed later with the RESULTS tooling. If `artifacts/stream_bench_v1.json` or `_v2.json` is missing, STOP and report.)

- [ ] **Step 6: Rehearse the evaluation (numbers only; charts come in Task 5)**

Run: `python scripts/final_evaluation.py --sim data/rehearsal_seed200 --n-boot 500`
Expected: prints the dataset line and F1 with intervals for every kind and detector, and writes `artifacts/final_eval_rehearsal_seed200.json`. Fix any pipeline bug you hit in the evaluation code (never a model or frozen file). Report the printed table, the run time and anything surprising (e.g. intervals that look implausibly narrow or wide, or a detector with zero alerts).

---

### Task 5: Charts and RESULTS.md generator

**Design rules (from the data-viz guidance, already validated):** detector colors are fixed by entity in this order: LightGBM `#2a78d6`, tuned rules `#eb6834`, textbook rules `#1baf7a`, generic rule `#eda100`, Isolation Forest `#e87ba4` (first five slots of the reference palette; the validator passes all checks, and the contrast warning for aqua/yellow/magenta is covered by the legend, direct labels and the full tables in RESULTS.md). Streaming engines use `#4a3aa7` (Python) and `#008300` (Java). Text is never in a series color. Lines 2 px, markers at least 8 px with a 2 px surface ring, hairline solid grid, legend always present, no dual axes, no number on every point.

**Files:** Create `src/sentinel/eval/charts.py`, `src/sentinel/eval/results_md.py`; Tests `tests/test_charts.py`, `tests/test_results_md.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_charts.py`:
```python
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
```
`tests/test_results_md.py`:
```python
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
                      "access_log": ["2026-09-24T00:00:00+00:00\tdata\\test_seed100"]}
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
```
Run: `python -m pytest tests/test_charts.py tests/test_results_md.py -v`. Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 2: Write `src/sentinel/eval/charts.py`**

```python
"""Figures for RESULTS.md. Fixed color per detector; thin marks; legend always; no dual axes."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
COLORS = {"LightGBM": "#2a78d6", "tuned rules": "#eb6834", "textbook rules": "#1baf7a",
          "generic rule": "#eda100", "Isolation Forest": "#e87ba4"}
ENGINE_COLORS = {"Python": "#4a3aa7", "Java": "#008300"}
ORDER = ("LightGBM", "tuned rules", "textbook rules", "generic rule", "Isolation Forest")
KINDS = ("SPOOFING", "LAYERING", "WASH")
LEVELS = ("easy", "medium", "hard")


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.grid(True, color=GRID, linewidth=0.6, linestyle="-")
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK2, labelsize=9, length=0)


def _fig(ncols: int, width: float, height: float):
    fig, axes = plt.subplots(1, ncols, figsize=(width, height), dpi=160, facecolor=SURFACE)
    return fig, np.atleast_1d(axes)


def pr_figure(pr: Mapping, path: Path) -> None:
    """Precision-recall curves per type. Curves: LightGBM, Isolation Forest. Points: rules. Ring: validation threshold."""
    fig, axes = _fig(3, 13, 4.6)
    for ax, kind in zip(axes, KINDS):
        _style(ax)
        d = pr[kind]
        for det in ("Isolation Forest", "LightGBM"):
            c = d["curves"][det]
            ax.plot(c["recall"], c["precision"], color=COLORS[det], linewidth=2, solid_capstyle="round",
                    solid_joinstyle="round")
        for det, (p, r) in d["points"].items():
            ax.plot([r], [p], marker="o", markersize=9, markerfacecolor=COLORS[det], markeredgecolor=SURFACE,
                    markeredgewidth=2, linestyle="none")
        for det, (p, r) in d["operating"].items():
            ax.plot([r], [p], marker="o", markersize=10, markerfacecolor=COLORS[det], markeredgecolor=SURFACE,
                    markeredgewidth=2, linestyle="none", zorder=5)
        p, r = d["operating"]["LightGBM"]
        ax.annotate("LightGBM at the validation threshold", xy=(r, p), xytext=(10, 12), textcoords="offset points",
                    fontsize=8, color=INK2)
        ax.set_xlim(0, 1.02)
        ax.set_ylim(0, 1.02)
        ax.set_xlabel("Recall", color=INK2, fontsize=9)
        if ax is axes[0]:
            ax.set_ylabel("Precision", color=INK2, fontsize=9)
        ap = d["ap"]
        ax.set_title(f"{kind.title()}", loc="left", color=INK, fontsize=11, pad=22)
        ax.text(0, 1.03, f"average precision: LightGBM {ap['LightGBM']:.2f}, Isolation Forest {ap['Isolation Forest']:.2f}",
                transform=ax.transAxes, color=INK2, fontsize=8)
    handles = [Line2D([0], [0], color=COLORS[d], linewidth=2, label=f"{d} (curve)") for d in ("LightGBM", "Isolation Forest")]
    handles += [Line2D([0], [0], marker="o", linestyle="none", markersize=9, markerfacecolor=COLORS[d],
                       markeredgecolor=SURFACE, markeredgewidth=2, label=f"{d} (single point)")
                for d in ("tuned rules", "textbook rules", "generic rule")]
    fig.legend(handles=handles, loc="lower center", ncol=5, frameon=False, labelcolor=INK2, fontsize=9)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def recall_figure(recall: Mapping, path: Path) -> None:
    """Window recall by difficulty for every detector, with 95% cluster-bootstrap intervals."""
    fig, axes = _fig(3, 13, 4.4)
    x = np.arange(len(LEVELS))
    width = 0.11
    offsets = (np.arange(len(ORDER)) - (len(ORDER) - 1) / 2) * (width + 0.02)
    for ax, kind in zip(axes, KINDS):
        _style(ax)
        for j, det in enumerate(ORDER):
            cells = [recall[kind][det][lvl] for lvl in LEVELS]
            vals = np.array([c["point"] for c in cells])
            lo = np.array([c["lo"] for c in cells])
            hi = np.array([c["hi"] for c in cells])
            ax.bar(x + offsets[j], vals, width, color=COLORS[det])
            ax.errorbar(x + offsets[j], vals, yerr=[vals - lo, hi - vals], fmt="none", ecolor=INK2,
                        elinewidth=1.1, capsize=2)
            if det == "LightGBM":
                for xi, v, h in zip(x + offsets[j], vals, hi):
                    ax.text(xi, min(h + 0.03, 1.04), f"{v:.2f}", ha="center", fontsize=8, color=INK)
        ax.set_xticks(x, [lvl.title() for lvl in LEVELS])
        ax.set_ylim(0, 1.1)
        ax.set_title(kind.title(), loc="left", color=INK, fontsize=11)
        if ax is axes[0]:
            ax.set_ylabel("Recall (windows)", color=INK2, fontsize=9)
    fig.legend(handles=[Patch(facecolor=COLORS[d], label=d) for d in ORDER], loc="lower center", ncol=5,
               frameon=False, labelcolor=INK2, fontsize=9)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def _human_ns(value: float, upper_bound: bool = False) -> str:
    text = f"{value / 1000:.1f} µs" if value >= 1000 else f"{value:.0f} ns"
    return ("≤" + text) if upper_bound else text


def performance_figure(bench: Mapping, path: Path) -> None:
    """Throughput and per-event latency of the Python reference and the Java engine."""
    fig, axes = _fig(2, 11, 4.4)
    ax1, ax2 = axes
    engines = (("Python", bench["python"]), ("Java", bench["java_v2"]))
    _style(ax1)
    for i, (name, run) in enumerate(engines):
        v = run["throughput_eps"] / 1e6
        ax1.bar(i, v, 0.4, color=ENGINE_COLORS[name])
        ax1.text(i, v * 1.02, f"{v:.2f}M/s", ha="center", fontsize=9, color=INK)
    ax1.set_xticks([0, 1], [e[0] for e in engines])
    ax1.set_ylabel("Events per second (millions)", color=INK2, fontsize=9)
    ax1.set_title("Throughput (single thread)", loc="left", color=INK, fontsize=11)
    ax1.set_ylim(0, max(e[1]["throughput_eps"] for e in engines) / 1e6 * 1.15)

    _style(ax2)
    metrics = (("median", "Median"), ("p99", "p99"), ("p999", "p99.9"))
    x = np.arange(len(metrics))
    width = 0.32
    for i, (name, run) in enumerate(engines):
        vals = [run["latency_ns"]["overall"][m] for m, _ in metrics]
        ax2.bar(x + (i - 0.5) * (width + 0.04), vals, width, color=ENGINE_COLORS[name])
        for xi, (m, _), v in zip(x + (i - 0.5) * (width + 0.04), metrics, vals):
            ax2.text(xi, v * 1.12, _human_ns(v, upper_bound=(name == "Java" and m == "median")),
                     ha="center", fontsize=8, color=INK)
    ax2.set_yscale("log")
    ax2.set_xticks(x, [label for _, label in metrics])
    ax2.set_ylabel("Latency per event (ns, log scale)", color=INK2, fontsize=9)
    ax2.set_title("Latency per event", loc="left", color=INK, fontsize=11)
    ax2.set_ylim(top=max(e[1]["latency_ns"]["overall"]["p999"] for e in engines) * 6)
    fig.legend(handles=[Patch(facecolor=ENGINE_COLORS[n], label=n) for n, _ in engines], loc="lower center", ncol=2,
               frameon=False, labelcolor=INK2, fontsize=9)
    fig.text(0.99, 0.01, "Java median is at the 100 ns timer resolution (Windows).", ha="right", fontsize=7.5, color=INK2)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
```

- [ ] **Step 3: Write `src/sentinel/eval/results_md.py`**

```python
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
    return f"{value / 1000:,.1f} µs" if value >= 1000 else f"{value:,.0f} ns"


def render_results(final: Mapping, bench: Mapping, manifest: Mapping[str, str], img_dir: str) -> str:
    """The full RESULTS.md text."""
    meta, detectors = final["meta"], final["detectors"]
    out = ["# Results", "",
           f"Evaluated on the untouched test simulation `{meta['sim_dir']}` ({meta['n_events']:,} events, "
           f"{meta['n_windows']:,} trader-windows of 10 s, {meta['n_manipulation_windows']:,} manipulation windows from "
           f"{meta['n_episodes']} injected episodes: " + ", ".join(f"{k.title()} {v}" for k, v in meta["windows_by_type"].items())
           + "). Accuracy numbers come from the **Java streaming engine's own alerts**; the Isolation Forest is scored in Python "
           "on the same windows. Every metric is followed by a 95% cluster-bootstrap interval "
           f"({meta['n_boot']} replicates; manipulation windows resampled by episode, normal windows by account). "
           "The data is synthetic: see the README's limitations before drawing conclusions about real markets.", "",
           f"![Precision-recall curves]({img_dir}/pr_curves.png)", "", "## Detection performance", ""]
    for kind in KINDS:
        title = "any manipulation type" if kind == "ANY" else kind.title()
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
                         f"{v['false_alerts_per_1000_innocent_windows']:.1f}", f"{v['false_alerts_per_instrument_hour']:.1f}"])
        out += [f"### {'Any type' if kind == 'ANY' else kind.title()}", "",
                _table(["Detector", "True alerts", "False alerts", "False alerts per 1,000 innocent windows",
                        "False alerts per instrument-hour"], rows), ""]

    env = bench["environment"]
    out += ["## Streaming performance", "",
            f"Single-threaded, {bench['n_events']:,} pre-loaded events, per-event timing of `process(event)` including model and "
            f"rule scoring when a window closes. {env['os']}, {env['cpu']}, {env['logical_cores']} logical cores, "
            f"Python {env['python']}, {env['java']}. Timer overhead is subtracted; Java's median sits at the 100 ns timer "
            "resolution of Windows, so read it as an upper bound.", "", f"![Streaming performance]({img_dir}/performance.png)", ""]
    rows = []
    for label, key in (("Python (reference)", "python"), ("Java v1 (straight port)", "java_v1"), ("Java v2 (flattened trees)", "java_v2")):
        run = bench[key]
        lat = run["latency_ns"]
        rows.append([label, f"{run['throughput_eps']:,.0f}", _ns(lat["overall"]["median"]), _ns(lat["overall"]["p99"]),
                     _ns(lat["overall"]["p999"]), _ns(lat["overall"]["max"]),
                     f"{_ns(lat['window_close']['median'])} (n={lat['window_close']['n']})", _ns(lat["other"]["median"])])
    out += [_table(["Engine", "Events/s", "Median", "p99", "p99.9", "Max", "Window-closing events: median", "Other events: median"], rows), "",
            "Window-closing events are the few (about one in 3,000) that trigger scoring for every active trader; they define the far tail.", "",
            "## Frozen artifacts and run log", "",
            "These files were hashed and tagged `stage4-frozen` before the test simulation was first loaded; the evaluation "
            "refuses to run if any hash differs.", "", _table(["File", "sha-256"], [[f"`{p}`", f"`{h[:16]}…`"] for p, h in manifest.items()]), "",
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
```

- [ ] **Step 4: Run tests, then render the rehearsal outputs and LOOK at them**

Run: `python -m pytest tests/test_charts.py tests/test_results_md.py -v` then `python -m pytest -q`. Expected: all pass.
Then run the rehearsal with outputs:
`python scripts/final_evaluation.py --sim data/rehearsal_seed200 --n-boot 500 --charts-dir artifacts/rehearsal_charts --results artifacts/RESULTS_rehearsal.md`
Open the three PNGs in `artifacts/rehearsal_charts/` with the Read tool (it displays images) and check for: overlapping labels or text, legend collisions, clipped titles, unreadable annotations, bars thicker than needed, wrong colors per detector (fixed order), a missing legend. Read `artifacts/RESULTS_rehearsal.md` and check that every table renders. Fix layout problems in `charts.py` and re-render until clean. Report what you fixed. (Do not commit `artifacts/`.)

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/eval/charts.py src/sentinel/eval/results_md.py tests/test_charts.py tests/test_results_md.py docs/bench
git commit -m "$(cat <<'EOF'
feat(eval): charts and generated RESULTS.md; commit Stage 3 benchmark JSON

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Freeze, then the single final run (controller only)

Executed by the controller, not a subagent, so the protocol stays intact.

- [ ] **Step 1:** `git status` must be clean and `python -m pytest -q` green. Confirm `data/test_access.log` does not exist (the test set has never been loaded).
- [ ] **Step 2:** `python scripts/freeze_artifacts.py`, then `git add docs/frozen-artifacts.json`, commit ("chore: freeze artifacts before the final test run"), and `git tag -a stage4-frozen -m "Models, thresholds, rules, extractor and Java sources frozen before the test simulation was loaded"`.
- [ ] **Step 3:** Run once: `python scripts/final_evaluation.py --sim data/test_seed100 --final --charts-dir docs/img --results RESULTS.md`. If it fails on an evaluation-code bug, fix only evaluation code, and add the fix to a "Run log" note in the final report; never change a frozen file (the manifest check would refuse anyway).
- [ ] **Step 4:** Read `RESULTS.md` and the three PNGs; commit `RESULTS.md` and `docs/img/*.png` ("results: final evaluation on the untouched test simulation").

---

## Self-review (against the spec)

**Spec coverage:** freeze and rehearsal protocol: Tasks 3, 4, 6; guarded event/feature loading: Task 0; Java-engine headline plus Python Isolation Forest: Task 4 (`run_java`, `engine_flags`, Isolation Forest scoring); metrics list (precision, recall, F1, FPR, episode recall, recall by difficulty, AP, alert volume): Tasks 1, 2, 4; cluster bootstrap by episode and account with 2,000 replicates and paired differences: Tasks 1 and 4; charts (PR curves, recall by difficulty, latency and throughput) and generated RESULTS.md with hashes and run log: Task 5; tests listed in the spec: bootstrap known answers and coverage (Task 1), metric/curve/volume hand cases (Task 2), freeze mismatch (Task 3), chart and RESULTS generation from a small dict (Task 5).

**Placeholder scan:** none; the one flagged duplicate line in `final.py` is called out with its exact fix.

**Consistency:** detector names (`generic rule`, `textbook rules`, `tuned rules`, `LightGBM`, `Isolation Forest`) are identical in `final.py`, `charts.py`, `results_md.py` and the tests; Java detector names (`generic`, `textbook`, `tuned`, `model`) match Stage 3; the results dict keys used by the renderers (`metrics`, `recall_by_difficulty`, `paired`, `alert_volume`, `pr`, `meta`) are produced in `evaluate`/`run_final_evaluation`; bench dict keys (`python`, `java_v1`, `java_v2`, `environment`, `n_events`) are produced in `build_outputs` and consumed in `render_results` and `performance_figure`.

**Risks the executor should expect:** chart layout needs the visual check (Task 5 Step 4); bootstrap coverage test may reveal a resampling bug; rehearsal may reveal pipeline bugs (good, that is its purpose); the `tests/test_final.py` helper is imported by the chart/results tests (`from tests.test_final import ...`), which works because `tests/__init__.py` exists.
