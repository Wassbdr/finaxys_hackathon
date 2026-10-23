"""Sources de données de l'API : un monde en direct (Redis) ou une partie enregistrée (replay)."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

from journal.writer import read_journal


class JournalTail:
    """Lit le journal de façon incrémentale (seules les nouvelles lignes sont parsées)."""

    def __init__(self):
        self.path: Path | None = None
        self.offset = 0
        self.llm_traces: list[dict] = []
        self.lock = threading.Lock()

    def refresh(self, path: Path | None) -> None:
        with self.lock:
            if path != self.path:
                self.path, self.offset, self.llm_traces = path, 0, []
            if not path or not path.exists():
                return
            with open(path, encoding="utf-8") as fh:
                fh.seek(self.offset)
                for line in fh:
                    if not line.endswith("\n"):
                        break
                    self.offset += len(line.encode("utf-8"))
                    entry = json.loads(line)
                    msg = entry["message"]
                    if msg["type"] == "llm_trace" and entry["signature_valid"]:
                        self.llm_traces.append({"tick": msg["tick"], "message_id": msg["message_id"], **msg["payload"]})


def filter_traces(traces: list[dict], agent: str | None, since_tick: int | None, limit: int) -> list[dict]:
    out = [t for t in traces if (agent is None or t["agent"] == agent) and (since_tick is None or t["tick"] >= since_tick)]
    return out[-limit:]


class LiveSource:
    kind = "live"

    def __init__(self, cfg: dict, client):
        self.cfg = cfg
        self.client = client
        self.prefix = cfg["redis"]["prefix"]
        self.tail = JournalTail()

    def _json(self, key: str):
        raw = self.client.get(f"{self.prefix}{key}")
        return json.loads(raw) if raw else None

    def state(self):
        return self._json("state")

    def graph(self):
        return self._json("graph")

    def run_dir(self) -> Path | None:
        raw = self.client.get(f"{self.prefix}run_dir")
        return Path(raw) if raw else None

    def llm_trace(self, agent=None, since_tick=None, limit=200):
        run = self.run_dir()
        self.tail.refresh(run / "journal.jsonl" if run else None)
        return filter_traces(self.tail.llm_traces, agent, since_tick, limit)

    async def stream(self, types: set[str] | None):
        pubsub = self.client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(f"{self.prefix}bus")
        try:
            while True:
                raw = pubsub.get_message(timeout=0.0)
                if raw is None:
                    await asyncio.sleep(0.05)
                    continue
                msg = json.loads(raw["data"])
                if types is None or msg["type"] in types:
                    yield msg
        finally:
            pubsub.close()


class ReplaySource:
    """Rejoue une partie enregistrée (states.jsonl + journal.jsonl) au rythme de l'horloge."""

    kind = "replay"

    def __init__(self, run_dir: str | Path, tick_seconds: float = 1.0, speed: float = 1.0, loop: bool = False):
        self.run = Path(run_dir)
        self.frames = [json.loads(line) for line in (self.run / "states.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        if not self.frames:
            raise ValueError(f"{self.run} : aucun état enregistré")
        self.messages: dict[int, list[dict]] = {}
        self.traces: list[dict] = []
        for entry in read_journal(self.run / "journal.jsonl"):
            msg = entry["message"]
            self.messages.setdefault(msg["tick"], []).append(msg)
            if msg["type"] == "llm_trace" and entry["signature_valid"]:
                self.traces.append({"tick": msg["tick"], "message_id": msg["message_id"], **msg["payload"]})
        self.period = tick_seconds / speed
        self.loop = loop
        self.started = time.monotonic()

    def index(self) -> int:
        i = int((time.monotonic() - self.started) / self.period)
        return i % len(self.frames) if self.loop else min(i, len(self.frames) - 1)

    def current_tick(self) -> int:
        return self.frames[self.index()]["tick"]

    def state(self):
        return {**self.frames[self.index()]["state"], "replay": True}

    def graph(self):
        return self.frames[self.index()]["graph"]

    def llm_trace(self, agent=None, since_tick=None, limit=200):
        now = self.current_tick()
        return filter_traces([t for t in self.traces if t["tick"] <= now], agent, since_tick, limit)

    async def stream(self, types: set[str] | None):
        sent_until = self.current_tick() - 1
        while True:
            now = self.current_tick()
            if now < sent_until:          # bouclage
                sent_until = now - 1
            for tick in range(sent_until + 1, now + 1):
                for msg in self.messages.get(tick, []):
                    if types is None or msg["type"] in types:
                        yield msg
            sent_until = now
            await asyncio.sleep(self.period / 2)
