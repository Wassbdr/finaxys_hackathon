"""Rejoue une partie enregistrée avec la même API (secours pour la démo).

    python -m api.replay runs/main [--speed 2] [--loop] [--port 8000]
"""

from __future__ import annotations

import argparse

import uvicorn

from api.app import create_app
from api.sources import ReplaySource
from sim.config import load_config


def main(argv=None) -> None:
    cfg = load_config()
    parser = argparse.ArgumentParser(prog="api.replay")
    parser.add_argument("run_dir")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--port", type=int, default=cfg["api"]["port"])
    args = parser.parse_args(argv)
    source = ReplaySource(args.run_dir, cfg["clock"]["tick_seconds"], args.speed, args.loop)
    print(f"[replay] {args.run_dir} : {len(source.frames)} ticks, vitesse x{args.speed}, http://127.0.0.1:{args.port}", flush=True)
    uvicorn.run(create_app({"main": source}), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
