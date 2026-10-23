"""The Simulation tab's film: each scenario of the last bench, replayed without (A, "theirs") and with (B, "ours")
the Vigie, cut down to what the 3D scene animates: who sends what to whom, each node's state, the money.

    python -m vigie.simulation        # after make metrics: runs/vigie-improve/improvements.json → vigie/dashboard-sim.json
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from sim.config import ROOT, load_config

OUT = ROOT / "vigie" / "dashboard-sim.json"
PITCH = "php2shell_day"
TRADER = re.compile(r"^trader-\d+")
FLOW = {"order_proposal": "order", "payment_proposal": "payment", "compliance_decision": "decision"}
MODE = {"compromised": "compromised", "down": "down", "stale": "serves stale data", "slow": "slows down", "normal": "back to normal"}
LEVEL = {"ORANGE": "turns {} orange", "ROUGE": "turns {} red", "QUARANTAINE": "quarantines {}"}


def k_eur(v):
    return f"€{v / 1e6:.2f}M" if abs(v) >= 1e6 else f"€{round(v / 1e3)}k"


def world(directory: Path, ticks: int) -> dict:
    """One world's film: forward-filled state changes, the messages that move, an English event line per thing that
    happened, and the money at each tick."""
    states = [json.loads(line) for line in (directory / "states.jsonl").open()]
    modes = {s["tick"]: {k: v["mode"] for k, v in s["state"]["services"].items()} for s in states}
    changes, events, msgs, last = [], [], [], {}

    def put(t, key, value):
        if last.get(key) != value:
            last[key] = value
            changes.append([t, key, value])

    pnl, fraud = [], []
    for s in states:
        t, st = s["tick"], s["state"]
        for svc, info in st["services"].items():
            if last.get("s:" + svc) not in (None, info["mode"]):
                events.append([t, "bad" if info["mode"] == "compromised" else "info", f"{svc} {MODE.get(info['mode'], info['mode'])}"])
            put(t, "s:" + svc, info["mode"])
        for a, info in st["agents"].items():
            if info["contaminated"] and not last.get("c:" + a):
                events.append([t, "bad", f"{a} acts on compromised data"])
            put(t, "c:" + a, int(info["contaminated"]))
            put(t, "z:" + a, int(info["paused"] or not info["alive"]))
        light = st["light"]["light"]
        if last.get("light") not in (None, light):
            events.append([t, {"VERT": "good", "ORANGE": "warn"}.get(light, "bad"), f"bank light {dict(VERT='green', ORANGE='orange', ROUGE='red')[light]}"])
        put(t, "light", light)
        pnl.append(round(st["pnl"]["trading_eur"]))
        fraud.append(round(-st["pnl"]["payment_fraud_eur"]))

    traders = sorted(a[2:] for a in last if a.startswith("c:trader-"))
    vigie_ticks, incidents, lost = set(), set(), set()
    for line in (directory / "journal.jsonl").open():
        m = json.loads(line)["message"]
        t, typ, p, src = m["tick"], m["type"], m["payload"], m["from"]
        bad = int(any(modes.get(d["tick"], {}).get(d["service"]) == "compromised" for d in m.get("data_provenance") or []))
        if src == "vigie" and not p.get("stub"):
            vigie_ticks.add(t)
        if typ == "price_update":
            msgs += [[t, "quant", to, "price", bad] for to in traders + ["risk"]]
        elif typ in FLOW:
            msgs.append([t, src, m["to"], FLOW[typ], bad])
        elif typ == "risk_decision":
            msgs += [[t, "risk", to, "brake" if m["to"] == "traders" else "decision", bad] for to in (traders if m["to"] == "traders" else [m["to"]])]
        elif typ == "execution":
            target = "payment" if p.get("kind") == "payment" else p.get("trader") or (TRADER.match(p.get("proposal_id", "")) or [None])[0]
            bad = int(bool(p.get("compromised_sources")))
            gate = p.get("gate") or {}
            if gate.get("decided_by") == "vigie":
                msgs.append([t, "executor", "vigie", "approve" if gate.get("approved") else "refuse", bad])
            if p.get("status") == "refused":
                what = "a payment" if p.get("kind") == "payment" else f"{target}'s order"
                # the sim writes some reasons in French: a timeout and the executor's own orange-light hold
                why = (", ".join(p.get("compromised_sources") or []) or {"timeout": "the Vigie did not answer in time"}.get(gate.get("decided_by"))
                       or str(gate.get("reason") or "").lower().replace("_", " ") or ("orange light, no irreversible action" if "ORANGE" in str(p.get("reason")) else ""))
                events.append([t, "good", f"{'the gate' if gate else 'the executor'} refuses {what}" + (f": {why}" if why else "")])
                continue
            if target:
                msgs.append([t, "executor", target, "exec", bad])
            if p.get("kind") == "payment" and p.get("status") == "executed":
                rerouted = p.get("iban_credited") != p.get("iban_requested")
                msgs.append([t, "executor", "payment-gateway", "fraud" if rerouted else "wire", int(rerouted)])
                if rerouted:
                    events.append([t, "bad", f"{k_eur(p['amount_eur'])} wired to a rerouted IBAN"])
        elif typ == "heartbeat" and src == "vigie" and m["to"] == "dashboard":
            st = p.get("state") or {}
            for a, info in (st.get("agents") or {}).items():
                level = "QUARANTAINE" if info.get("quarantined") else info.get("level", "VERT")
                if level in LEVEL and last.get("v:" + a) != level:
                    events.append([t, "good", "the Vigie " + LEVEL[level].format(a)])
                put(t, "v:" + a, level)
            iso = sorted(st.get("isolated_services") or [])
            for svc in set(iso) - set((last.get("iso") or "").split(",")) - {""}:
                events.append([t, "good", f"the Vigie isolates {svc}"])
            put(t, "iso", ",".join(iso))
        elif typ == "alert":
            kind = p.get("kind")
            if kind == "cve_published":
                cve = p.get("cve") or {}
                events.append([t, "warn", f"{cve.get('name') or cve.get('id', 'a')} CVE published (CVSS {cve.get('cvss', '?')})"])
            elif kind == "trader_stop":
                events.append([t, "warn", f"{src} stops trading: daily loss over its limit"])
            elif kind == "compliance_block":
                events.append([t, "good", f"compliance blocks {p.get('target')}: KYC refused"])
            elif kind == "invalid_signature":
                events.append([t, "good", f"a message signed as {p.get('claimed_from')} is rejected: unknown key"])
            elif kind == "market_event":
                events.append([t, "info", f"real market news: {p.get('asset')} {p.get('pct'):+}%"])
            elif kind == "payment_held":
                events.append([t, "info", f"payment holds the invoice to {(p.get('invoice') or {}).get('beneficiary_id')}"])
            elif kind == "vigie_heartbeat_lost" and t not in lost:
                lost.add(t)
                events.append([t, "warn", "the agents lose the Vigie's heartbeat: each goes orange on its own"])
            elif kind == "vigie_incident" and p.get("incident_id") not in incidents and p.get("action") == "red":
                incidents.add(p.get("incident_id"))
                events.append([t, "good", f"the Vigie turns the bank red over {p.get('agent')}"])

    for t in range(ticks):
        put(t, "vigie", int(t in vigie_ticks))
    changes.sort(key=lambda c: c[0])
    events.sort(key=lambda e: e[0])
    return {"changes": changes, "msgs": msgs, "events": events, "pnl": pnl, "fraud": fraud}


def scenario(directory: Path, cfg: dict) -> dict:
    last = json.loads((directory / "A" / "states.jsonl").read_text().splitlines()[-1])
    graph, ticks = last["graph"], last["tick"] + 1
    profiles = cfg["agents"]["trader"]["profiles"]
    agents = [{"id": n["id"], "role": n["role"], **({"profile": profiles[int(n["id"].split("-")[1]) - 1]} if n["role"] == "trader" else {})}
              for n in graph["nodes"] if n["kind"] == "agent"]
    reads = [[e["source"], e["target"], round(e["calls"] / ticks, 3), int(e["hidden"])]
             for e in graph["edges"] if e.get("kind") == "data" and not e.get("transitive") and e.get("calls")]
    return {"ticks": ticks, "agents": agents, "services": [n["id"] for n in graph["nodes"] if n["kind"] == "service"], "reads": reads,
            "worlds": {"theirs": world(directory / "A", ticks), "ours": world(directory / "B", ticks)}}


def main() -> None:
    cfg = load_config()
    report = json.loads((ROOT / cfg["runs_dir"] / "vigie-improve" / "improvements.json").read_text())
    base = ROOT / report["evidence_dir"] / "baseline"
    names = sorted((d.name for d in base.iterdir() if (d / "A").is_dir() and (d / "B").is_dir()), key=lambda n: (n != PITCH, n))
    film = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "evidence_dir": report["evidence_dir"],
            "scenarios": {n: scenario(base / n, cfg) for n in names}}
    OUT.write_text(json.dumps(film, separators=(",", ":"), ensure_ascii=False))
    print(f"[simulation] {len(names)} scenarios from {report['evidence_dir']} → {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
