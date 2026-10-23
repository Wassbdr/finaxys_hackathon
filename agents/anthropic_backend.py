"""Backend Claude API : un appel Messages par jugement, sortie JSON contrainte par schéma.

- Modèle : ANTHROPIC_MODEL dans .env (défaut claude-opus-5-5).
- Pas de `temperature` : l'API (version 2026-10-21) la refuse ; la trace l'indique.
- `fallbacks: "default"` : si les classifieurs de sécurité déclinent, l'API relance la requête
  sur le modèle de repli recommandé. Un refus final lève une erreur et l'agent retombe sur les règles.
"""

from __future__ import annotations

import json
import os

import anthropic

ASSET_SCORE = {
    "type": "object",
    "properties": {"asset": {"type": "string"}, "score": {"type": "number"}},
    "required": ["asset", "score"],
    "additionalProperties": False,
}
SCHEMAS = {
    "news_sentiment": {
        "type": "object",
        "properties": {
            "sentiment": {"type": "array", "items": ASSET_SCORE},
            "action": {"type": "string", "enum": ["none", "sell_all"]},
            "confidence": {"type": "number"},
            "justification": {"type": "string"},
        },
        "required": ["sentiment", "action", "confidence", "justification"],
        "additionalProperties": False,
    },
    "kyc_review": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["approve", "reject"]},
            "confidence": {"type": "number"},
            "justification": {"type": "string"},
        },
        "required": ["verdict", "confidence", "justification"],
        "additionalProperties": False,
    },
    "justify": {
        "type": "object",
        "properties": {"justification": {"type": "string"}, "confidence": {"type": "number"}},
        "required": ["justification", "confidence"],
        "additionalProperties": False,
    },
}


class LLMRefusal(Exception):
    pass


class AnthropicBackend:
    def __init__(self, cfg: dict, client: anthropic.Anthropic | None = None):
        llm = cfg["llm"]
        self.model = os.environ.get("ANTHROPIC_MODEL") or llm.get("model", "claude-opus-5-5")
        self.max_tokens = llm["max_tokens"]
        self.effort = llm.get("effort", "low")
        self.client = client or anthropic.Anthropic(timeout=llm.get("timeout_s", 30.0), max_retries=1)

    def complete(self, system: str, user: str, task: str, context: dict) -> tuple[dict, dict]:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMAS[task]}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        tokens = {"input": response.usage.input_tokens, "output": response.usage.output_tokens}
        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None) if response.stop_details else None
            raise LLMRefusal(f"refus du modèle (catégorie {category})")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise ValueError(f"réponse sans texte (stop_reason {response.stop_reason})")
        output = json.loads(text)
        if task == "news_sentiment":
            output["sentiment"] = {item["asset"]: item["score"] for item in output["sentiment"]}
        output["_served_by"] = response.model
        return output, tokens
