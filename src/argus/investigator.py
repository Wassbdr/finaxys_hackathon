"""Evidence-bounded investigation reports with an optional OpenAI-compatible LLM."""
from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen
from typing import Any


def investigate(case: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    evidence_ids = {int(event["id"]) for event in events}
    evidence = [{"id": e["id"], "time_s": round(e["ts"] / 1_000_000_000, 3),
                "agent": e["trader_id"], "type": e["event_type"], "side": e["side"],
                "price": e["price"], "quantity": e["qty"]} for e in events]
    alerts = case.get("alerts", [])
    if not evidence:
        return {"source": "evidence template", "summary": "No linked events were available for this case.",
                "findings": [], "limitations": ["The case has no retrievable event evidence."],
                "evidence_ids": []}

    behavioral_links = sum(edge.get("kind") == "behavior" for edge in case["graph"].get("edges", []))
    summary = (f"{len(case['agents'])} agents showed repeated same-side order activity "
               f"within a 150 ms interval. The graph contains "
               f"{behavioral_links} repeated behavioral relationships. "
               f"Sentinel produced {len(alerts)} individual surveillance alert(s) for these agents. "
               "This pattern warrants review; it does not establish intent or misconduct.")
    report: dict[str, Any] = {
        "source": "evidence template", "summary": summary,
        "findings": [{"text": "Repeated synchronized orders or cancellations were observed between the linked agents.",
                      "evidence_ids": sorted(evidence_ids)}],
        "agents": case["agents"],
        "related_alerts": alerts,
        "limitations": ["Synchronized activity can have legitimate explanations.",
                        "Synthetic simulation evidence cannot establish real-world misconduct."],
        "evidence_ids": sorted(evidence_ids),
    }
    key = os.environ.get("ARGUS_LLM_API_KEY")
    if not key:
        return report
    base = os.environ.get("ARGUS_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("ARGUS_LLM_MODEL", "gpt-4o-mini")
    prompt = {
        "case": {"title": case["title"], "agents": case["agents"], "graph": case["graph"],
                 "sentinel_alerts": alerts},
        "events": evidence,
        "instruction": "Return JSON with summary, findings (text and evidence_ids), agents, limitations, "
                       "and evidence_ids. Cite only supplied event IDs. Describe suspicion, never proven intent. "
                       "Do not recommend or execute restrictions.",
    }
    request = Request(f"{base}/chat/completions", data=json.dumps({
        "model": model, "temperature": 0, "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": "You are a financial surveillance analyst. "
                     "Use only supplied synthetic evidence. State uncertainty clearly."},
                     {"role": "user", "content": json.dumps(prompt)}],
    }).encode(), headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=20) as response:
            body = json.loads(response.read())
        content = json.loads(body["choices"][0]["message"]["content"])
        claimed_ids = {int(value) for value in content.get("evidence_ids", [])}
        for finding in content.get("findings", []):
            claimed_ids.update(int(value) for value in finding.get("evidence_ids", []))
        if not claimed_ids.issubset(evidence_ids):
            raise ValueError("LLM returned an event reference absent from the case evidence")
        content["evidence_ids"] = sorted(claimed_ids)
        content["source"] = f"LLM: {model}"
        content.setdefault("limitations", []).append("AI analysis is advisory and does not establish misconduct.")
        return content
    except Exception as exc:
        report["llm_error"] = f"LLM unavailable; showing evidence template ({type(exc).__name__})."
        return report
