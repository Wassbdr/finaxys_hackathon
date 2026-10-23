"""Comparaison gel / couche 3 initiale / sans Vigie sur la graine réservée."""
import argparse
import hashlib
import json
from pathlib import Path

from vigie.eval import evaluate, SCENARIOS
from vigie.freeze import load_profile


def compare(seed=42):
    profile = load_profile()
    if not profile or seed in profile["training_seeds"]:
        raise ValueError("L'évaluation exige une graine absente de l'entraînement")
    profile_path = Path(__file__).with_name("freeze_profile.json")
    digest = hashlib.sha256(profile_path.read_bytes()).hexdigest()
    cases = []
    for name in SCENARIOS:
        frozen = evaluate(name, seed=seed)
        baseline = evaluate(name, seed=seed, freeze_profile=None)
        frozen["couche3_sans_gel"] = baseline["avec_couche3"]
        frozen["delta_gel_eur"] = round(frozen["avec_couche3"]["pnl_net_eur"] - baseline["avec_couche3"]["pnl_net_eur"], 2)
        cases.append(frozen)
    # Les paramètres n'ont pas été modifiés entre les scénarios ni à leur lecture.
    if hashlib.sha256(profile_path.read_bytes()).hexdigest() != digest:
        raise RuntimeError("Le profil a changé pendant l'évaluation")
    return {"seed": seed, "training_seeds": profile["training_seeds"], "profile_sha256": digest,
            "profile": profile, "cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    import sys
    from vigie.testing import deny_network
    sys.addaudithook(deny_network)
    result = compare(args.seed)
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    Path(args.out).write_text(rendered)
    print(rendered)


if __name__ == "__main__":
    main()
