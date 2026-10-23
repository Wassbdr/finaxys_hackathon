"""Interface phase 0 proposée aux deux développeurs ; vue du bus uniquement."""
from dataclasses import asdict, dataclass, field
import copy


@dataclass(frozen=True, init=False)
class Finding:
    tick: int
    layer: int
    kind: str
    target: str
    severity: str
    evidence: list[str]
    score: float

    def __init__(self, tick, layer, kind, target, severity, evidence=None, score=0.0, detail=None):
        for name, value in zip(('tick', 'layer', 'kind', 'target', 'severity', 'evidence', 'score'),
                               (tick, layer, kind, target, severity, evidence or [], score)):
            object.__setattr__(self, name, value)
        # Les détails du tableau de bord restent une extension de présentation ;
        # les sept champs du contrat et leur sérialisation dataclass sont inchangés.
        object.__setattr__(self, '_detail', detail or {})

    @property
    def detail(self):
        return self._detail

    def as_dict(self):
        return {**asdict(self), 'detail': self.detail}


@dataclass
class VigieState:
    tick: int = 0
    messages: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def observe(self, msg, keyring):
        try:
            if not keyring.verify(msg) or msg["message_id"] in self.messages:
                return False
            if type(msg["tick"]) is not int or msg["tick"] < 0:
                return False
            if not isinstance(msg["payload"], dict) or not isinstance(msg["depends_on"], list):
                return False
            # La projection exclut les annotations de vérité ajoutées par le simulateur.
            fields = {
                "tick": ("tick",),
                "price_update": ("assets", "as_of_tick", "age_ticks"),
                "heartbeat": ("view", "agent", "role"),
                "risk_decision": ("risk_view", "kind", "approved", "notional_eur"),
                "execution": ("status", "exec_id", "kind", "qty", "fill_price", "amount_eur", "notional_eur",
                              "trader", "asset", "side", "iban_requested", "iban_credited"),
                "alert": ("kind", "cve", "asset", "pct"),
                "human_decision": ("action", "target"),
                "order_proposal": ("asset", "side", "notional_eur", "signals", "trader", "quant_confidence", "news_refs", "justification"),
                "payment_proposal": ("amount_eur", "beneficiary_id"),
                "llm_trace": ("task", "output", "external", "response", "prompt_excerpt"),
            }
            record = {k: copy.deepcopy(msg[k]) for k in ("message_id", "tick", "from", "to", "type", "depends_on")}
            record["payload"] = {k: copy.deepcopy(msg["payload"][k]) for k in fields.get(msg["type"], ()) if k in msg["payload"]}
            record["data_provenance"] = [
                {k: entry[k] for k in ("service", "version", "response_hash", "tick") if k in entry}
                for entry in msg.get("data_provenance", []) if isinstance(entry, dict)]
            self.messages[msg["message_id"]] = record
            self.tick = max(self.tick, msg["tick"])
            return True
        except (KeyError, TypeError, ValueError):
            return False


def __getattr__(name):
    # L'import différé conserve les anciens points d'entrée sans cycle avec
    # Runtime, qui dépend lui-même du contrat de ce module.
    if name in {'Controller', 'Refused'}:
        from vigie import control
        return getattr(control, name)
    raise AttributeError(name)
