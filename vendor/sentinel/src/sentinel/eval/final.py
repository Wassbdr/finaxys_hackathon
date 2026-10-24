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
    """One metric cell: point estimate and interval bounds."""
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
    """Current git commit hash, or 'unknown' outside a repository."""
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
    df = load_sim(sim_dir, final=final, rebuild=True)
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
