"""Génère le Procfile à partir de config.yaml (un processus par agent, trader_count traders) et le lance.

    python -m sim.launch procfile [--no-llm] [--no-vigie] [--out Procfile.generated]
    python -m sim.launch run [--no-llm] [--no-vigie]

`run` est un petit superviseur : contrairement à honcho, il ne coupe pas tout quand un agent est
débranché (`inject kill trader-2`), sauf si c'est le broker ou le monde qui s'arrête.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from sim.config import ROOT, load_config, trader_ids


def python_bin() -> str:
    venv = ROOT / ".venv" / "bin" / "python"
    return str(venv.relative_to(ROOT)) if venv.exists() else Path(sys.executable).name


def procfile(cfg: dict, no_llm: bool, no_vigie: bool) -> str:
    py = python_bin()
    agent_flags = (" --no-llm" if no_llm else "") + (" --no-vigie" if no_vigie else "")
    lines = [
        f"broker: {py} -m bus.broker",
        f"world: {py} -m sim.world" + (" --no-vigie" if no_vigie else ""),
        f"journal: {py} -m journal.writer run",
        f"api: {py} -m api.app",
    ]
    lines += [f"svc_{sid.replace('-', '_')}: {py} -m services.run {sid}" for sid in cfg["services"]]
    lines.append(f"quant: {py} -m agents.quant{agent_flags}")
    lines += [f"{tid.replace('-', '_')}: {py} -m agents.trader --id {tid}{agent_flags}" for tid in trader_ids(cfg)]
    lines += [f"{a}: {py} -m agents.{a}{agent_flags}" for a in ("compliance", "risk", "payment")]
    if not no_vigie and cfg["vigie"]["stub"]:
        lines.append(f"vigie_stub: {py} -m bus.vigie_stub")
    return "\n".join(lines) + "\n"


ESSENTIAL = {"broker", "world"}


def parse_procfile(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        if line.strip() and not line.startswith("#") and ":" in line:
            name, cmd = line.split(":", 1)
            out.append((name.strip(), cmd.strip()))
    return out


def supervise(entries: list[tuple[str, str]], essential: set[str] = ESSENTIAL) -> int:
    width = max(len(n) for n, _ in entries) + 1
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    procs: dict[str, subprocess.Popen] = {}
    print_lock = threading.Lock()

    def pump(name: str, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            with print_lock:
                sys.stdout.write(f"{time.strftime('%H:%M:%S')} {name:<{width}}| {line}")
                sys.stdout.flush()

    for name, cmd in entries:
        proc = subprocess.Popen(cmd, shell=True, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, start_new_session=True)
        procs[name] = proc
        threading.Thread(target=pump, args=(name, proc), daemon=True).start()
        if name == "broker":
            time.sleep(1.0)

    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    reported: set[str] = set()
    while not stopping:
        for name, proc in procs.items():
            code = proc.poll()
            if code is not None and name not in reported:
                reported.add(name)
                print(f"[launch] {name} arrêté (code {code})", flush=True)
                if name in essential:
                    stopping = True
        time.sleep(0.3)
    print("[launch] arrêt de tous les processus", flush=True)
    for proc in procs.values():
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
    deadline = time.time() + 5
    for proc in procs.values():
        try:
            proc.wait(timeout=max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
    return 0


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="sim.launch")
    parser.add_argument("command", choices=["procfile", "run"], nargs="?", default="procfile")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--no-vigie", action="store_true")
    parser.add_argument("--out", default="Procfile.generated")
    args = parser.parse_args(argv)
    cfg = load_config()
    text = procfile(cfg, args.no_llm, args.no_vigie)
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"{args.out} écrit ({cfg['agents']['trader']['count']} traders, LLM {'non' if args.no_llm else 'oui'}, Vigie {'non' if args.no_vigie else 'oui'})")
    if args.command == "run":
        sys.exit(supervise(parse_procfile(text)))


if __name__ == "__main__":
    main()
