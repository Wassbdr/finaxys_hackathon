"""Apprentissage borné hors graine 42 ; aucun ajustement sur l'évaluation finale."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from sim.config import load_config
from sim.inject import load_scenario
from vigie.core import VigieState
from vigie.eval import play
from vigie.freeze import Freeze, validate_profile

TRAINING_SEEDS = (7, 11, 19, 23, 31, 53)


def replay(samples, profile):
    manager = Freeze(VigieState(), (), profile)
    ticks = set()
    for sample in samples:
        manager.update(sample["frames"], sample["tick"])
        if manager.active:
            ticks.add(sample["tick"])
    return {"starts": sum(t["event"] == "start" for t in manager.transitions), "frozen_ticks": len(ticks)}


def train(seeds=TRAINING_SEEDS):
    if not seeds or 42 in seeds:
        raise ValueError("42 est réservé à l'évaluation, jamais à l'entraînement")
    observations, baselines = [], defaultdict(list)
    for seed in seeds:
        cfg = load_config(overrides={"seed": seed, "llm": {"enabled": False, "cache": False},
                                     "vigie": {"freeze_profile": None}})
        for name in ("clean", "rumeur_mimetisme"):
            scenario = ({"name": "clean", "duration": "180s", "steps": []} if name == "clean" else
                        load_scenario(Path(__file__).resolve().parents[1] / "scenarios/rumeur_mimetisme.yaml"))
            result = play(cfg, scenario, None, False, capture_frames=True)
            frames = result["freeze_frames"]
            observations.append({"seed": seed, "scenario": name, "samples": frames})
            if name == "clean":
                # Une seule observation par actif/tick pour ne pas surpondérer
                # les ticks où les traders publient beaucoup de messages.
                last = {(s["tick"], f["asset"]): f for s in frames for f in s["frames"] if f["index"] is not None}
                for f in last.values():
                    baselines[f["asset"]].append(f["index"])
    reference = {asset: sum(values) / len(values) for asset, values in sorted(baselines.items())}
    common = {"version": 1, "training_seeds": list(seeds), "reference": reference,
        "window_ticks": 30, "freeze_ticks": 10, "similarity_threshold": .98}
    trials = []
    # Grille fixée AVANT tout run sur 42. Priorité stricte : aucun début de gel
    # sur les propres d'entraînement, puis couverture temporelle de la rumeur.
    for margin in (0.0, .05, .15, .30, .40):
        for minimum in (2, 3):
            for persistence in (1, 3, 5, 10, 20):
                profile = {**common, "margin": margin, "min_aligned": minimum, "persistence_ticks": persistence}
                scores = [{"seed": row["seed"], "scenario": row["scenario"], **replay(row["samples"], profile)} for row in observations]
                false = sum(row["starts"] for row in scores if row["scenario"] == "clean")
                coverage = sum(row["frozen_ticks"] for row in scores if row["scenario"] != "clean")
                trials.append({"profile": profile, "clean_starts": false, "rumour_frozen_ticks": coverage, "scores": scores})
    best = min(trials, key=lambda t: (t["clean_starts"], -t["rumour_frozen_ticks"],
        t["profile"]["margin"], t["profile"]["min_aligned"], t["profile"]["persistence_ticks"]))
    profile = validate_profile(best["profile"])
    digest = hashlib.sha256(json.dumps(observations, sort_keys=True).encode()).hexdigest()
    report = {"training_seeds": list(seeds), "held_out_seed": 42, "candidate_count": len(trials),
        "selection": "min clean_starts, max rumour_frozen_ticks, then margin/min_aligned/persistence",
        "observable_training_sha256": digest, "selected": best,
        "nonselected_summary": [{"margin": t["profile"]["margin"], "min_aligned": t["profile"]["min_aligned"],
            "persistence_ticks": t["profile"]["persistence_ticks"], "clean_starts": t["clean_starts"],
            "rumour_frozen_ticks": t["rumour_frozen_ticks"]} for t in trials if t is not best]}
    return profile, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=str(Path(__file__).with_name("freeze_profile.json")))
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    import sys
    from vigie.testing import deny_network
    sys.addaudithook(deny_network)
    profile, report = train()
    Path(args.profile).write_text(json.dumps(profile, indent=2) + "\n")
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["selected"], indent=2))


if __name__ == "__main__":
    main()
