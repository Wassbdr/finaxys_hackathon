"""Adaptateur de lancement ; le simulateur et ses agents restent ceux de l'équipe."""

import argparse
import os
import json
import shlex
import time
from pathlib import Path

import yaml

from sim.config import load_config
from sim.launch import parse_procfile, procfile, python_bin, supervise


def runtime_config(cfg, no_vigie=False):
    import copy

    cfg = copy.deepcopy(cfg)
    cfg.pop("_path", None)
    cfg["llm"]["enabled"] = False
    cfg["vigie"].update(enabled=not no_vigie, stub=False)
    cfg["gate"].update(
        impl="passthrough" if no_vigie else "bus", default_on_timeout="reject"
    )
    return cfg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["procfile", "run"])
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--no-vigie", action="store_true")
    args = parser.parse_args()
    cfg = runtime_config(load_config(), args.no_vigie)
    path = Path(cfg["runs_dir"]) / "vigie-config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True))
    os.environ.update(HORIZON_CONFIG=str(path), HORIZON_NO_LLM="1")
    text = procfile(cfg, True, args.no_vigie)
    if not args.no_vigie:
        from vigie.compare import learn_reference

        reference = Path(cfg["runs_dir"]) / f"vigie-reference-{time.time_ns()}"
        profiles = learn_reference(cfg, 120, reference)
        profile_path = reference / "profiles.json"
        profile_path.write_text(json.dumps(profiles))
        cfg["vigie"]["profiles_path"] = str(profile_path)
        path.write_text(yaml.safe_dump(cfg, allow_unicode=True))
        text += f"vigie: {python_bin()} -m vigie --profiles {shlex.quote(str(profile_path))}\n"
    # Honcho lancé ensuite doit hériter du même profil que le superviseur.
    text = (
        "\n".join(
            f"{name}: env HORIZON_CONFIG={shlex.quote(str(path))} HORIZON_NO_LLM=1 {cmd}"
            for name, cmd in parse_procfile(text)
        )
        + "\n"
    )
    Path("Procfile.generated").write_text(text)
    print(f"Procfile.generated : gate={cfg['gate']['impl']}, stub=false, LLM désactivé")
    if args.command == "run":
        raise SystemExit(supervise(parse_procfile(text)))


if __name__ == "__main__":
    main()
