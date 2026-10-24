"""Train every detector on seeds 7/8/9 and write the VALIDATION report.

python scripts/train_detectors.py [--rebuild]

Uses only data/sim_seed7, sim_seed8 and sim_seed9. The final test simulation
(data/test_seed100) is never loaded here. All numbers written are validation
numbers: thresholds, rule tuning and early stopping were selected on this same
data, so they are optimistic. The honest numbers come from the test set in Stage 4.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from sentinel.detect.alerts import model_alerts, render_alert, rule_alerts
from sentinel.detect.dataset import CLASS_INDEX, load_sim, time_split, to_xy
from sentinel.detect.explain import ModelExplainer
from sentinel.detect.model import (IsolationForestDetector, choose_thresholds, predict_proba, predict_types,
                                   save_model, train_lgbm)
from sentinel.detect.rules import GENERIC_RULE, TEXTBOOK_RULES, save_rules, tune_rule
from sentinel.eval.metrics import recall_by_difficulty, results_table
from sentinel.features.extractor import FEATURE_NAMES

SIMS = ("sim_seed7", "sim_seed8", "sim_seed9")
KINDS = ("SPOOFING", "LAYERING", "WASH")


def single_feature_auc(train: pd.DataFrame) -> pd.DataFrame:
    """Best single-feature AUC per type (type vs NORMAL): a check that no one feature gives the answer away."""
    rows = []
    for kind in KINDS:
        sub = train[train["label"].isin([kind, "NORMAL"])]
        y = (sub["label"] == kind).to_numpy()
        aucs = {f: roc_auc_score(y, sub[f]) for f in FEATURE_NAMES if sub[f].nunique() > 1}
        feature, auc = max(aucs.items(), key=lambda kv: abs(kv[1] - 0.5))
        rows.append({"type": kind, "best_feature": feature, "auc": auc if auc >= 0.5 else 1 - auc})
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()

    full = pd.concat([load_sim(args.data / s, rebuild=args.rebuild) for s in SIMS], ignore_index=True)
    train, val = time_split(full)
    print(f"train {len(train):,} windows ({(train.label != 'NORMAL').sum()} manipulation), "
          f"validation {len(val):,} ({(val.label != 'NORMAL').sum()} manipulation)")

    # -- rules ---------------------------------------------------------------
    tuned = {kind: tune_rule(rule, val, seed=0) for kind, rule in TEXTBOOK_RULES.items()}
    generic = GENERIC_RULE.evaluate(val)
    preds: dict[str, dict[str, np.ndarray]] = {
        "generic rule (untyped)": {"ANY": generic, **{k: generic for k in KINDS}},
        "textbook rules": {k: TEXTBOOK_RULES[k].evaluate(val) for k in KINDS},
        "tuned rules": {k: tuned[k].evaluate(val) for k in KINDS},
    }

    # -- LightGBM ------------------------------------------------------------
    booster = train_lgbm(train, val, seed=0)
    X_val = to_xy(val)[0]
    proba = predict_proba(booster, X_val)
    thresholds = choose_thresholds(proba, val["label"])
    types = predict_types(proba, thresholds)
    lgbm = {k: types == CLASS_INDEX[k] for k in KINDS}
    preds["LightGBM"] = {**lgbm, "ANY": types != 0}

    # -- Isolation Forest ----------------------------------------------------
    forest = IsolationForestDetector(seed=0).fit(to_xy(train)[0])
    scores = forest.score(X_val)
    iso_threshold = forest.choose_threshold(scores, val["label"])
    flagged = scores >= iso_threshold
    preds["Isolation Forest"] = {"ANY": flagged, **{k: flagged for k in KINDS}}

    table = results_table(val["label"], preds)
    recall = recall_by_difficulty(val, preds)
    auc = single_feature_auc(train)

    # -- persist + report ----------------------------------------------------
    save_model(booster, thresholds, Path("models"))
    save_rules(Path("models") / "tuned_rules.json", tuned)
    forest.save(Path("models") / "iforest.joblib")
    (Path("models") / "iforest_threshold.json").write_text(json.dumps({"threshold": iso_threshold}), encoding="utf-8")
    reference = {n: float(np.median(train.loc[train.label == "NORMAL", n])) for n in FEATURE_NAMES}
    (Path("models") / "normal_reference.json").write_text(json.dumps(reference, indent=2), encoding="utf-8")
    explainer = ModelExplainer(booster, reference)
    alerts = model_alerts(val, proba, thresholds, explainer)
    examples = [a for a in alerts if a.kind == "SPOOFING"][:1] + [a for a in alerts if a.kind == "LAYERING"][:1] \
        + [a for a in alerts if a.kind == "WASH"][:1]
    rule_examples = rule_alerts(val, tuned["WASH"], "tuned")[:1]

    out = Path("artifacts")
    out.mkdir(exist_ok=True)
    fmt = {"precision": "{:.3f}", "recall": "{:.3f}", "f1": "{:.3f}", "fpr": "{:.4f}"}
    report = ["# Validation report (Stage 2)", "",
              "Validation numbers only: rule tuning, thresholds and early stopping used this same data, so they are "
              "optimistic. Final numbers come from the untouched test simulation in Stage 4.", "",
              f"Model: {booster.num_trees()} trees. Thresholds: {thresholds}. Isolation Forest threshold: {iso_threshold:.4f}.",
              "", "## Precision / recall / F1 / FPR", "",
              table.round({"precision": 3, "recall": 3, "f1": 3, "fpr": 4}).to_markdown(index=False), "",
              "## Recall by difficulty", "", recall.round(3).to_markdown(index=False), "",
              "## Best single-feature AUC on training data (leakage check)", "", auc.round(3).to_markdown(index=False), "",
              "## Tuned rules", ""]
    report += [f"- {k}: " + " AND ".join(f"{c.feature} {c.op} {c.threshold:.3g}" for c in r.conditions)
               for k, r in tuned.items()]
    report += ["", "## Example alerts", ""]
    report += ["```", *[render_alert(a) for a in examples + rule_examples], "```"]
    (out / "validation_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    main()
