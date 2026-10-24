"""Journal central append-only : JSONL chaîné par hash.

Chaque entrée contient le hash de la précédente ; modifier ou supprimer une ligne casse la chaîne.

    python -m journal.writer run       # écrit tous les messages du bus
    python -m journal.writer verify    # vérifie la chaîne du journal courant
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterator

from bus.message import Keyring, canonical, sha256

GENESIS = "0" * 64


def entry_hash(prev_hash: str, seq: int, signature_valid: bool, message: dict) -> str:
    return sha256(prev_hash + canonical({"seq": seq, "signature_valid": signature_valid, "message": message}))


class JournalWriter:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.seq = 0
        self.last_hash = GENESIS
        if self.path.exists():
            for entry in read_journal(self.path):
                self.seq, self.last_hash = entry["seq"], entry["hash"]
        self._fh = open(self.path, "a", encoding="utf-8")

    def append(self, message: dict, signature_valid: bool) -> dict:
        self.seq += 1
        entry = {
            "seq": self.seq,
            "prev_hash": self.last_hash,
            "hash": entry_hash(self.last_hash, self.seq, signature_valid, message),
            "signature_valid": signature_valid,
            "message": message,
        }
        self._fh.write(canonical(entry) + "\n")
        self._fh.flush()
        self.last_hash = entry["hash"]
        return entry

    def close(self) -> None:
        self._fh.close()


def read_journal(path: str | Path) -> Iterator[dict]:
    path = Path(path)
    if not path.exists():
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def verify_journal(path: str | Path) -> tuple[bool, int, int | None]:
    """(chaîne intacte, nombre d'entrées, numéro de la première entrée invalide)."""
    prev, count = GENESIS, 0
    for count, entry in enumerate(read_journal(path), start=1):
        expected = entry_hash(prev, entry["seq"], entry["signature_valid"], entry["message"])
        if entry["prev_hash"] != prev or entry["hash"] != expected or entry["seq"] != count:
            return False, count, count
        prev = entry["hash"]
    return True, count, None


def run_dir(cfg: dict) -> Path:
    world = cfg["redis"]["prefix"].strip(":") or "main"
    return Path(cfg["runs_dir"]) / world


def journal_path(cfg: dict) -> Path:
    return run_dir(cfg) / "journal.jsonl"


def run(cfg: dict) -> None:
    from bus.redis_bus import RedisBus, connect
    from sim.config import known_senders

    client = connect(cfg["redis"]["url"])
    bus = RedisBus(client, cfg["redis"]["prefix"])
    keyring = Keyring(known_senders(cfg))
    run_key = f"{cfg['redis']['prefix']}run_dir"
    current, writer = None, None
    print(f"[journal] en attente du monde ({run_key})", flush=True)
    for msg in bus.listen(timeout=0.5):
        target = client.get(run_key)
        if target and target != current:
            if writer:
                writer.close()
            current, writer = target, JournalWriter(Path(target) / "journal.jsonl")
            print(f"[journal] écriture dans {writer.path}", flush=True)
        if msg is not None and writer is not None:
            writer.append(msg, keyring.verify(msg))


def main(argv: list[str] | None = None) -> None:
    from sim.config import load_config

    parser = argparse.ArgumentParser(prog="journal.writer")
    parser.add_argument("command", choices=["run", "verify"])
    parser.add_argument("path", nargs="?")
    args = parser.parse_args(argv)
    cfg = load_config()
    if args.command == "run":
        run(cfg)
        return
    path = args.path or journal_path(cfg)
    ok, count, bad = verify_journal(path)
    if ok:
        print(f"{path} : chaîne intacte ({count} entrées)")
    else:
        print(f"{path} : chaîne ROMPUE à l'entrée {bad}")
        sys.exit(1)


if __name__ == "__main__":
    main()
