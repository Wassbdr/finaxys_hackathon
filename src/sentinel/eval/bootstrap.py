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
    """Per-episode and per-account flag counts: (tp_e, n_e, detected_e, fp_a, n_a)."""
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
