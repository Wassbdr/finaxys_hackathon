"""Socle commun des agents : heartbeat, feux, règle du heartbeat Vigie, émission signée.

Un agent ne fait aucune I/O directe : services, LLM et paramètres lui sont injectés par le runtime
(processus Redis ou lockstep). Ses contrôles sont VOLONTAIREMENT locaux : il ne voit ni les cumuls
des autres agents ni la provenance globale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bus.lights import LIGHT_AUTHORITIES, ORANGE, ROUGE, VERT, worst
from bus.message import MessageFactory, merge_provenance
from sim.config import declared_dependencies, role_of


@dataclass
class Context:
    services: Any          # LocalServiceClient | HttpServiceClient
    llm: Any               # LLMJudge
    params: Any            # Params
    keyring: Any
    vigie_enabled: bool = True
    deterministic: bool = True


class Agent:
    observes: frozenset[str] = frozenset()

    def __init__(self, agent_id: str, cfg: dict, ctx: Context):
        self.id = agent_id
        self.role = role_of(agent_id)
        self.cfg = cfg
        self.ctx = ctx
        self.factory = MessageFactory(agent_id, ctx.keyring, deterministic=ctx.deterministic)
        self.depends_on = declared_dependencies(cfg).get(agent_id, [])
        self.tick = 0
        self.bus_light = VERT
        self.last_vigie_heartbeat: int | None = None
        self.first_tick: int | None = None
        self.vigie_lost = False
        self.out: list[dict] = []
        self.notes: list[str] = []   # derniers événements, exposés dans le heartbeat

    # --- feux -----------------------------------------------------------------------------
    @property
    def light(self) -> str:
        return worst(self.bus_light, ORANGE) if self.vigie_lost else self.bus_light

    @property
    def paused(self) -> bool:
        return self.light == ROUGE

    @property
    def amount_factor(self) -> float:
        return self.cfg["vigie"]["orange_amount_factor"] if self.light == ORANGE else 1.0

    def _check_vigie_heartbeat(self) -> None:
        if not self.ctx.vigie_enabled:
            self.vigie_lost = False
            return
        reference = self.last_vigie_heartbeat if self.last_vigie_heartbeat is not None else self.first_tick
        lost = self.tick - reference > self.cfg["vigie"]["heartbeat_timeout_ticks"]
        if lost and not self.vigie_lost:
            self.emit("alert", "human", {
                "kind": "vigie_heartbeat_lost",
                "severity": "warning",
                "text": f"{self.id} : plus de heartbeat Vigie depuis le tick {reference}, passage local en ORANGE.",
            })
        self.vigie_lost = lost

    # --- émission -------------------------------------------------------------------------
    def emit(self, type: str, to: str, payload: dict, depends_on=(), provenance=()) -> dict:
        msg = self.factory.make(self.tick, type, to, payload, list(depends_on), merge_provenance(provenance))
        self.out.append(msg)
        return msg

    def note(self, text: str) -> None:
        self.notes = (self.notes + [f"t{self.tick} {text}"])[-5:]

    def ask_llm(self, task: str, instructions: str, context: dict, external: list[dict], provenance: list[dict]) -> dict:
        judgement = self.ctx.llm.judge(self.id, self.role, task, instructions, context, external, provenance)
        self.emit("llm_trace", "*", judgement.trace, provenance=provenance)
        return judgement.output

    # --- boucle ---------------------------------------------------------------------------
    def handle(self, msg: dict) -> list[dict]:
        self.out = []
        kind, sender = msg["type"], msg["from"]
        if kind == "tick":
            self.tick = msg["payload"]["tick"]
            if self.first_tick is None:
                self.first_tick = self.tick
            self._check_vigie_heartbeat()
            if not self.paused:
                self.on_tick()
            self.emit("heartbeat", "*", self.heartbeat_payload())
        elif kind == "heartbeat" and sender == "vigie":
            self.last_vigie_heartbeat = msg["tick"]
            self.vigie_lost = False
        elif kind == "light_change" and sender in LIGHT_AUTHORITIES:
            self.bus_light = msg["payload"]["light"]
            self.note(f"feu {self.bus_light} ({sender})")
        elif kind == "human_decision" and sender in ("human", "inject"):
            if msg["payload"].get("action") == "resume" and self.bus_light == ROUGE:
                self.bus_light = VERT
                self.note("relance humaine")
            self.on_human_decision(msg)
        elif kind in ("human_decision", "light_change"):
            self.note(f"{kind} ignoré : {sender} n'a pas autorité")
        elif not self.paused:
            handler = getattr(self, f"on_{kind}", None)
            if handler and msg["from"] != self.id:
                handler(msg)
        return self.out

    def heartbeat_payload(self) -> dict:
        return {
            "agent": self.id,
            "role": self.role,
            "depends_on": self.depends_on,
            "light": self.light,
            "bus_light": self.bus_light,
            "vigie_lost": self.vigie_lost,
            "paused": self.paused,
            "notes": self.notes,
            **self.status(),
        }

    # À surcharger
    def status(self) -> dict:
        return {}

    def on_tick(self) -> None:
        pass

    def on_human_decision(self, msg: dict) -> None:
        pass
