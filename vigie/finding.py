"""The one shape every Vigie layer reports in (Wassim's plan, phase 0)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Finding:
    tick: int
    layer: int
    kind: str
    target: str
    severity: str  # info | warning | critical
    evidence: list[str] = field(default_factory=list)  # message_ids
    score: float = 0.0
    detail: dict = field(default_factory=dict)

    def as_dict(self):
        return asdict(self)
