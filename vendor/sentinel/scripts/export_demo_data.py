"""Export replayable stories for the Sentinel demo page (docs/demo/story_data.json).

python scripts/export_demo_data.py [--sim data/rehearsal_seed200]

Each story is a short stretch of simulated trading: order-book frames every 100 ms with the focus trader's
resting quantity marked, the focus trader's events, plain-language chapters, and what each detector said about
the focus trader's 10-second window. The simulation is deterministic, so it is re-run with a recording hook and
checked to reproduce the saved event log exactly. Never used with the final test simulation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from sentinel.detect.dataset import CLASS_INDEX, CLASSES, load_sim
from sentinel.detect.explain import ModelExplainer
from sentinel.detect.model import IsolationForestDetector, predict_proba, predict_types
from sentinel.features.extractor import FEATURE_NAMES
from sentinel.sim import runner
from sentinel.sim.exchange import Exchange
from sentinel.sim.runner import SimConfig, simulate_instrument
from sentinel.sim.types import ASK, BID
from sentinel.stream.engine import load_engine_parts, rule_flags

NS = 1_000_000_000
FRAME_NS = 100_000_000
LEVELS = 6
WINDOW_NS = 10 * NS


class DemoExchange(Exchange):
    """Exchange that records the order-book ladder at fixed intervals inside chosen time ranges."""

    specs: list[dict] = []

    def _record(self, ts, etype, order_id, trader_id, side, price, qty, episode, fills):  # noqa: D401
        super()._record(ts, etype, order_id, trader_id, side, price, qty, episode, fills)
        for spec in DemoExchange.specs:
            if spec["index"] == self.index and spec["t0"] <= self.last_ts <= spec["t1"] and self.last_ts >= spec["next"]:
                spec["frames"].append(_frame(self, spec))
                spec["next"] = self.last_ts + FRAME_NS


def _frame(ex: Exchange, spec: dict) -> dict:
    focus = spec["focus"]
    book = ex.book
    out = {"t": round((ex.last_ts - spec["t0"]) / NS, 3)}
    for name, side in (("bids", BID), ("asks", ASK)):
        prices = book._prices[side]
        chosen = prices[::-1][:LEVELS] if side == BID else prices[:LEVELS]
        rows = {p: [int(p), int(book._qty[side][p]), 0] for p in chosen}
        for order in book.orders.values():
            if order.trader_id in focus and order.side == side and order.price in rows:
                rows[order.price][2] += int(order.qty)
        out[name] = [rows[p] for p in chosen]
    bb, ba = ex.snap[0], ex.snap[1]
    out["mid"] = (bb + ba) / 2 if bb and ba else None
    return out


def _clean(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    return v


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sim", type=Path, default=Path("data/rehearsal_seed200"))
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    parser.add_argument("--out", type=Path, default=Path("docs/demo/story_data.json"))
    args = parser.parse_args()
    if args.sim.name.startswith("test_"):
        sys.exit("refusing to use the final test simulation")

    df = load_sim(args.sim)
    episodes = pd.read_parquet(args.sim / "episodes.parquet")
    events = pd.read_parquet(args.sim / "events.parquet")
    events["instrument"] = events["instrument"].astype(str)
    seed = int(args.sim.name.rsplit("seed", 1)[1])
    cfg = SimConfig(seed=seed)

    booster, thresholds, rule_sets = load_engine_parts(args.model_dir)
    X = df[list(FEATURE_NAMES)].to_numpy(dtype=np.float64)
    proba = predict_proba(booster, X)
    types = predict_types(proba, thresholds)
    forest = IsolationForestDetector.load(args.model_dir / "iforest.joblib")
    iso_threshold = json.loads((args.model_dir / "iforest_threshold.json").read_text())["threshold"]
    iso_score = forest.score(X)
    train_normal = load_sim(Path("data/sim_seed7"))
    reference = {n: float(np.median(train_normal.loc[train_normal.label == "NORMAL", n])) for n in FEATURE_NAMES}
    explainer = ModelExplainer(booster, reference)

    flags = {(name, rule.kind): rule_flags(rule, X) for name, rules in rule_sets.items() for rule in rules}

    # -- choose the stories ---------------------------------------------------
    def duration_ok(ep) -> bool:
        return (ep.t_end - ep.t_start) <= 14 * NS

    def best_window(ep, kind):
        rows = df[(df["episode_id"] == ep.episode_id) & (df["trader_id"] == ep.trader_a)]
        if rows.empty:
            return None
        k = CLASS_INDEX[kind]
        i = rows.index[int(np.argmax(proba[rows.index, k]))]
        return int(i)

    def candidates(kind, levels):
        out = []
        for ep in episodes[(episodes["kind"] == kind) & (episodes["difficulty"].isin(levels))].itertuples():
            if not duration_ok(ep):
                continue
            i = best_window(ep, kind)
            if i is not None:
                out.append((float(proba[i, CLASS_INDEX[kind]]), ep, i))
        return out

    stories_spec = []
    mm = df[(df["trader_id"] % 1000 < 6) & (df["label"] == "NORMAL") & (df["cancel_rate"] >= 0.9) & (types == 0)]
    mm = mm[(mm["n_new"] > 20)].sort_values("n_new")
    if mm.empty:
        sys.exit("no market-maker window found")
    row = mm.iloc[len(mm) // 2]
    stories_spec.append({"key": "market_maker", "kind": "NORMAL", "index": int(mm.index[len(mm) // 2]), "episode": None,
                         "focus": {int(row["trader_id"])}, "t0": int(row["window_start_ns"]) + 1 * NS,
                         "t1": int(row["window_start_ns"]) + 9 * NS, "difficulty": "none", "hit": None})
    for kind, key, levels in (("SPOOFING", "spoofing", ("easy", "medium")), ("LAYERING", "layering", ("easy", "medium")),
                              ("WASH", "wash", ("easy", "medium"))):
        alerted = [c for c in candidates(kind, levels) if types[c[2]] == CLASS_INDEX[kind]]
        alerted.sort(key=lambda c: -c[0])
        score, ep, i = alerted[0]
        focus = {int(ep.trader_a)} | ({int(ep.trader_b)} if ep.trader_b >= 0 else set())
        stories_spec.append({"key": key, "kind": kind, "index": i, "episode": ep, "focus": focus,
                             "t0": int(ep.t_start) - 2 * NS, "t1": int(ep.t_end) + 3 * NS, "difficulty": ep.difficulty, "hit": True})
    # A clean miss: a hard spoof that no detector flags at all, kept short enough to watch.
    missed = [c for c in candidates("SPOOFING", ("hard",))
              if types[c[2]] == 0 and not any(f[c[2]] for f in flags.values()) and (c[1].t_end - c[1].t_start) <= 8 * NS]
    if not missed:
        sys.exit("no clean missed spoof found")
    missed.sort(key=lambda c: c[0])
    score, ep, i = missed[len(missed) // 2]
    stories_spec.append({"key": "miss", "kind": "SPOOFING", "index": i, "episode": ep, "focus": {int(ep.trader_a)},
                         "t0": int(ep.t_start) - 2 * NS, "t1": int(ep.t_end) + 3 * NS, "difficulty": "hard", "hit": False})

    # -- re-simulate with the recording hook ------------------------------------
    names = list(cfg.instruments)
    for spec in stories_spec:
        r = df.loc[spec["index"]]
        spec["instrument"] = str(r["instrument"])
        spec["inst_index"] = names.index(spec["instrument"])
    DemoExchange.specs = [{"index": s["inst_index"], "t0": s["t0"], "t1": s["t1"], "next": s["t0"], "focus": s["focus"],
                           "frames": [], "story": s["key"]} for s in stories_spec]
    original = runner.Exchange
    runner.Exchange = DemoExchange
    try:
        for inst_index in sorted({s["inst_index"] for s in stories_spec}):
            replay, _, _ = simulate_instrument(cfg, inst_index)
            saved = events[events["instrument"] == names[inst_index]].reset_index(drop=True)
            same = (len(replay) == len(saved) and np.array_equal(replay["ts"].to_numpy(), saved["ts"].to_numpy())
                    and np.array_equal(replay["price"].to_numpy(), saved["price"].to_numpy())
                    and np.array_equal(replay["qty"].to_numpy(), saved["qty"].to_numpy()))
            if not same:
                sys.exit(f"re-simulation of {names[inst_index]} does not match the saved events; refusing to export")
            print(f"{names[inst_index]}: re-simulation reproduces the saved event log ({len(saved):,} events)")
    finally:
        runner.Exchange = original
    frames_by_story = {s["story"]: s["frames"] for s in DemoExchange.specs}

    # -- assemble each story ---------------------------------------------------
    def describe_window(i: int, kind: str) -> dict:
        r = df.loc[i]
        feats = {n: _clean(r[n]) for n in FEATURE_NAMES}
        p = [float(x) for x in proba[i]]
        top_class = int(np.argmax(p[1:]) + 1)
        alerted = int(types[i]) != 0
        cls = int(types[i]) if alerted else top_class
        x = X[i]
        expl = [{"feature": c.feature, "value": c.value, "typical": c.reference, "shap": c.contribution, "phrase": c.phrase}
                for c in explainer.explain(x, cls, top_k=5)]
        return {
            "features": feats, "typical": reference, "proba": p,
            "model": {"alerted": alerted, "kind": CLASSES[cls], "score": p[cls]},
            "explanation": expl,
            "rules": {"generic": bool(flags[("generic", "ANY")][i]),
                      "textbook": [k for (n, k), f in flags.items() if n == "textbook" and f[i]],
                      "tuned": [k for (n, k), f in flags.items() if n == "tuned" and f[i]]},
            "iforest": {"flagged": bool(iso_score[i] >= iso_threshold), "score": float(iso_score[i]), "threshold": float(iso_threshold)},
            "window_id": [int(r["trader_id"]), str(r["instrument"]), int(r["window"])],
            "window_start_s": float((int(r["window"]) * WINDOW_NS - 0) / NS),
        }

    stories = []
    for spec in stories_spec:
        t0, t1, focus = spec["t0"], spec["t1"], spec["focus"]
        ev = events[(events["instrument"] == spec["instrument"]) & (events["trader_id"].isin(focus))
                    & (events["ts"] >= t0) & (events["ts"] <= t1)]
        et = ev["event_type"].astype(str).to_numpy()
        rows = [{"t": round((int(a) - t0) / NS, 3), "type": e, "side": int(s), "price": int(pr), "qty": int(q),
                 "trader": int(tr), "order": int(o)}
                for a, e, s, pr, q, tr, o in zip(ev["ts"], et, ev["side"], ev["price"], ev["qty"], ev["trader_id"], ev["order_id"])]
        story = {"key": spec["key"], "kind": spec["kind"], "difficulty": spec["difficulty"], "instrument": spec["instrument"],
                 "focus": sorted(focus), "duration_s": round((t1 - t0) / NS, 3), "frames": frames_by_story[spec["key"]],
                 "events": rows, "verdict": describe_window(spec["index"], spec["kind"]),
                 "episode": None if spec["episode"] is None else {
                     "start_s": round((int(spec["episode"].t_start) - t0) / NS, 3),
                     "end_s": round((int(spec["episode"].t_end) - t0) / NS, 3), "side": int(spec["episode"].side)}}
        w = story["verdict"]["window_id"][2]
        story["window"] = {"start_s": round((w * WINDOW_NS - t0) / NS, 3), "end_s": round(((w + 1) * WINDOW_NS - t0) / NS, 3)}
        stories.append(story)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"source": args.sim.name, "stories": stories}, separators=(",", ":")), encoding="utf-8")
    for s in stories:
        v = s["verdict"]
        print(f"{s['key']:13s} {s['instrument']} focus {s['focus']} {s['duration_s']:.1f}s frames {len(s['frames'])} events {len(s['events'])} "
              f"| model {v['model']['kind']} {v['model']['score']:.2f} alerted={bool(v['model']['alerted'])} "
              f"| generic={v['rules']['generic']} textbook={v['rules']['textbook']} tuned={v['rules']['tuned']} iforest={v['iforest']['flagged']}")
    print(f"wrote {args.out} ({args.out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
