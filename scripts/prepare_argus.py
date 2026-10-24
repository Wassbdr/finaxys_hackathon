"""Reproducible small-market training. Seeds 61/62 train; 63 validates; 64 evaluates."""
import json
from pathlib import Path
import pandas as pd
from sklearn.metrics import precision_recall_fscore_support
from sentinel.sim.runner import SimConfig, simulate
from sentinel.sim.labels import build_labels
from sentinel.features.extractor import extract_features, FEATURE_NAMES
from sentinel.detect.model import train_lgbm, choose_thresholds, save_model, predict_types
from sentinel.detect.dataset import to_xy


def sample(seed):
    res = simulate(SimConfig(instruments=("SYN/USD",), n_market_makers=2, n_momentum=2, n_noise=6,
        events_per_instrument=60000, episodes_per_instrument=35, seed=seed))
    labels = build_labels(res.events, res.event_episode, res.episodes, 10_000_000_000)
    df = extract_features(res.events).merge(labels, on=["trader_id", "instrument", "window"], validate="one_to_one")
    print(f"Seed {seed}: {len(res.events):,} events; {len(df)} windows", flush=True)
    return df


if __name__ == "__main__":
    train = pd.concat([sample(61), sample(62)], ignore_index=True)
    val = sample(63)
    booster = train_lgbm(train, val, seed=17, max_rounds=200)
    thresholds = choose_thresholds(booster.predict(to_xy(val)[0], num_threads=1), val.label)
    out = Path("models/argus")
    save_model(booster, thresholds, out)
    (out / "reference.json").write_text(json.dumps(train.loc[train.label == "NORMAL", list(FEATURE_NAMES)].median().to_dict(), indent=2))
    test = sample(64)
    X, y = to_xy(test)
    pred = predict_types(booster.predict(X, num_threads=1), thresholds)
    p, r, f, _ = precision_recall_fscore_support(y != 0, pred != 0, average="binary", zero_division=0)
    report = {"train_seeds": [61,62], "validation_seed":63, "evaluation_seed":64,
              "precision":float(p), "recall":float(r), "f1":float(f), "evaluation_windows":len(y),
              "scope":"Individual manipulation in the 10-agent simulator. Does not evaluate coordination or real markets."}
    (out / "evaluation.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
