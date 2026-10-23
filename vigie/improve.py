"""Improvement layer: replays the scenarios, grades every Vigie rule and proposes changes to a human.

The simulator's answer key (compromised modes, trapped operations) is read here and only
here, to grade the Vigie after the fact; nothing in this file feeds the live brake.
A threshold change is proposed only once a replay of every scenario has measured it.
The same replays feed the bench metrics (report key "metrics", see vigie/METRIQUES.md): the Vigie is
calibrated on a seed of its own, never on the seed it is measured on.

    python -m vigie.improve                       # every scenario -> runs/vigie-improve/improvements.json
    python -m vigie.improve --scenarios php2shell_day,ledger_legacy
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time

import yaml

from bus.message import Keyring, canonical
from sim.clock import Clock
from sim.config import ROOT, agent_ids, known_senders, load_config
from sim.inject import load_scenario, scenario_steps
from vigie.compare import learn_reference
from vigie.detect import THRESHOLDS, Indicators
from vigie.state import VigieState

CANDIDATES = {
    "price_drift_z": [3, 4, 5, 6, 8],
    "reconciliation_gap_pct": [0.5, 1, 2, 5, 10],
    "herding_share": [0.67, 1.0],
    "breach_horizon_ticks": [5, 10, 20],
}
BRAKE_RULES = {"price_drift_z", "reconciliation_gap_pct"}  # the only ones that mark a service
ATTACKS = {"compromise", "stale", "down", "slow", "fake-agent", "kill", "kill-vigie", "change-iban", "invoice"}
CLEAN = "reference_propre"
CALIBRATION_SEED = 1
CLEAN_SEEDS = tuple(range(101, 111))
FORGED_REASONS = {"INVALID_SIGNATURE", "UNKNOWN_AGENT"}
NAMING_FINDINGS = {"impact_radius", "anomaly"}  # layer-1 findings whose target can be an attacked entity


def scenario_paths():
    return sorted([*(ROOT / "scenarios").glob("*.yaml"), *(ROOT / "vigie" / "scenarios").glob("*.yaml")])


def read_journal(run):
    with open(run / "journal.jsonl", encoding="utf-8") as fh:
        return [json.loads(line)["message"] for line in fh]


def read_states(run):
    with open(run / "states.jsonl", encoding="utf-8") as fh:
        return {s["tick"]: s["state"] for s in map(json.loads, fh)}


def compare(scenario_path, out, thresholds, workdir, seed=None, profiles=None):
    """One A/B replay in its own process: the Vigie subprocess reads the thresholds from HORIZON_CONFIG."""
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    cfg["vigie"]["thresholds"] = thresholds
    if seed is not None:
        cfg["seed"] = seed
    config = workdir / f"config-{out.parent.name}-{out.name}.yaml"
    config.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    env = {**os.environ, "HORIZON_CONFIG": str(config), "HORIZON_NO_LLM": "1"}
    cmd = [sys.executable, "-m", "vigie.compare", "--scenario", str(scenario_path), "--out", str(out)]
    if profiles:
        cmd += ["--profiles", str(profiles)]
    done = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f"{scenario_path.name}: {done.stderr.strip().splitlines()[-1:]}")
    return json.loads((out / "comparison.json").read_text(encoding="utf-8"))


def compromised_spans(states):
    """The answer key, from the store's per-tick states: services announce nothing on the bus."""
    spans = {}
    for tick in sorted(states):
        for sid, info in states[tick]["services"].items():
            mine = spans.get(sid, [])
            is_open = bool(mine) and mine[-1][1] is None
            if info["mode"] == "compromised" and not is_open:
                spans.setdefault(sid, []).append([info.get("since_tick", tick), None])
            elif info["mode"] != "compromised" and is_open:
                mine[-1][1] = tick
    return spans


def tainted(service, tick, spans):
    return any(start <= tick and (end is None or tick < end) for start, end in spans.get(service, []))


def attack_tick(scenario, cfg):
    ticks = [t for t, args in scenario_steps(scenario, Clock.from_config(cfg)) if args and args[0] in ATTACKS]
    ticks += [o["tick"] for o in scenario.get("vigie_orders", [])]
    return min(ticks, default=None)


def news_ticks(scenario, cfg):
    return {t for t, args in scenario_steps(scenario, Clock.from_config(cfg)) if args and args[0] == "news"}


def replay_indicators(cfg, run, thresholds):
    """The indicators see what the live Vigie sees: its own bus-built view, never the store."""
    ind, keys, view = Indicators(cfg, thresholds), Keyring(known_senders(cfg)), VigieState(cfg)
    alerts = []
    for m in read_journal(run):
        ind.observe(m, view.read())
        view.observe(m, keys)
        alerts += ind.drain()
    return alerts


def grade_alerts(alerts, spans, start):
    for a in alerts:
        if a["rule"] == "cve_unpatchable":
            a["true"] = a["service"] in spans  # a warning before the compromise counts
        elif a["service"]:
            a["true"] = tainted(a["service"], a["tick"], spans)
        else:
            a["true"] = start is not None and a["tick"] >= start
    return alerts


def chain(msg, by_id, spans, depth=6):
    steps = []
    while msg and len(steps) < depth:
        p = msg["payload"]
        steps.append(
            {
                "tick": msg["tick"],
                "from": msg["from"],
                "type": msg["type"],
                "fed_by_compromised": sorted({e["service"] for e in msg["data_provenance"] if tainted(e["service"], e["tick"], spans)}),
                "said": (p.get("justification") or p.get("reason") or "")[:140],
            }
        )
        parents = [by_id[i] for i in msg["depends_on"] if i in by_id]
        msg = parents[0] if parents else None
    return steps


def grade_run(name, comparison, run_dir, cfg, scenario, thresholds):
    b = run_dir / "B"
    messages = read_journal(b)
    by_id = {m["message_id"]: m for m in messages}
    spans = compromised_spans(read_states(b))
    start = attack_tick(scenario, cfg)
    refusals = defaultdict(lambda: {"count": 0, "contaminated": 0, "clean": 0, "blocked_eur": 0.0, "frozen_eur": 0.0})
    for m in messages:
        p = m["payload"]
        if m["type"] != "execution" or p.get("status") != "refused":
            continue
        rule = f"{p['decided_by']}: {p['reason'].removeprefix('gate : ')}"
        decision = by_id.get((m["depends_on"] or [None])[0], {})
        amount = float(decision.get("payload", {}).get("notional_eur", 0))
        proposal = decision.get("payload", {}).get("proposal", {}).get("message_id", "")
        bad = "corrupted:" in proposal or any(tainted(e["service"], e["tick"], spans) for e in m["data_provenance"])
        r = refusals[rule]
        r["count"] += 1
        r["contaminated" if bad else "clean"] += 1
        r["blocked_eur" if bad else "frozen_eur"] += amount
    executions = {m["payload"].get("exec_id"): m for m in messages if m["type"] == "execution"}
    final = read_states(b)
    trapped = final[max(final)]["trapped_operations"]
    missed = [
        {
            "tick": t["tick"],
            "kind": t.get("kind"),
            "agent": t.get("agent"),
            "amount_eur": t.get("amount_eur"),
            "target": t.get("asset") or t.get("beneficiary_id"),
            "compromised_sources": t.get("compromised_sources"),
            "chain": chain(executions.get(t["exec_id"]), by_id, spans),
        }
        for t in trapped
    ]
    a_alerts = grade_alerts(replay_indicators(cfg, run_dir / "A", thresholds), compromised_spans(read_states(run_dir / "A")), start)
    detection = []
    for service, service_spans in spans.items():
        hits = [a for a in a_alerts if a["service"] == service and a["true"]]
        first = min(hits, key=lambda a: a["tick"], default=None)
        detection.append(
            {
                "service": service,
                "compromised_at": service_spans[0][0],
                "detected_at": first and first["tick"],
                "by_rule": first and first["rule"],
                "ticks_to_detect": first and first["tick"] - service_spans[0][0],
            }
        )
    A, B = comparison["A"], comparison["B"]
    trading = lambda m: m["pnl_net_eur"] + m["fraud_loss_eur"]
    return {
        "scenario": name,
        "attack_starts_at": start,
        "unprotected_net_eur": A["pnl_net_eur"],
        "protected_net_eur": B["pnl_net_eur"],
        "avoided_loss_eur": comparison["avoided_loss_eur"],
        "avoided_fraud_eur": comparison["avoided_fraud_eur"],
        "brake_cost_eur": round(min(0.0, trading(B) - trading(A)), 2),
        "frozen_eur": round(sum(r["frozen_eur"] for r in refusals.values()), 2),
        "contaminated_operations": {"unprotected": A["trapped_operations"], "protected": B["trapped_operations"]},
        "refusals": dict(refusals),
        "detection": detection,
        "indicator_alerts": [{k: a[k] for k in ("tick", "rule", "target", "service", "value", "true", "text")} for a in a_alerts],
        "missed": missed,
        "news_ticks": sorted(news_ticks(scenario, cfg)),
    }


def sweep(cfg, runs, scenarios, thresholds):
    """Detection only, on the unprotected journals: no replay needed to count alerts."""
    table = {}
    for rule, values in CANDIDATES.items():
        rows = []
        for value in sorted({*values, thresholds[rule]}):
            caught, true, false, delay = set(), 0, 0, 0
            for name, run in runs.items():
                spans, start = compromised_spans(read_states(run / "A")), attack_tick(scenarios[name], cfg)
                alerts = [a for a in replay_indicators(cfg, run / "A", {**thresholds, rule: value}) if a["rule"] == rule]
                grade_alerts(alerts, spans, start)
                true += sum(a["true"] for a in alerts)
                false += sum(not a["true"] for a in alerts)
                for a in alerts:
                    key = (name, a["service"] or a["target"])
                    if a["true"] and key not in caught:
                        caught.add(key)
                        origin = spans[a["service"]][0][0] if a["service"] in spans else start
                        delay += a["tick"] - origin
            rows.append({"value": value, "caught": sorted(f"{n}:{s}" for n, s in caught), "true_alerts": true, "false_alerts": false, "ticks_to_detect": delay})
        table[rule] = rows
    return table


def best(rows, current):
    # Catch at least as much, then fewer false alerts, then sooner, then the closest to today.
    return min(rows, key=lambda r: (-len(r["caught"]), r["false_alerts"], r["ticks_to_detect"], abs(r["value"] - current)))


def totals(graded):
    return {
        "avoided_loss_eur": round(sum(g["avoided_loss_eur"] for g in graded), 2),
        "brake_cost_eur": round(sum(g["brake_cost_eur"] for g in graded), 2),
        "frozen_eur": round(sum(g["frozen_eur"] for g in graded), 2),
        "missed_operations": sum(len(g["missed"]) for g in graded),
    }


def forged(run):
    with open(run / "journal.jsonl", encoding="utf-8") as fh:
        return sum(not e["signature_valid"] and not e["message"]["from"].startswith("svc:") for e in map(json.loads, fh))


def operations(messages, spans):
    """Every executor outcome, contaminated or not by the answer key."""
    by_id = {m["message_id"]: m for m in messages}
    out = []
    for m in messages:
        if m["type"] != "execution":
            continue
        p = m["payload"]
        decision = by_id.get((m["depends_on"] or [None])[0], {}).get("payload", {})
        proposal = decision.get("proposal", {}).get("message_id", "")
        sources = {e["service"] for e in m["data_provenance"] if tainted(e["service"], e["tick"], spans)} | set(p.get("compromised_sources") or [])
        if "corrupted:" in proposal:
            sources.add(proposal.split(":")[0])
        out.append({"tick": m["tick"], "executed": p["status"] == "executed", "bad": bool(sources), "sources": sources, "eur": float(decision.get("notional_eur", 0)), "by": p.get("decided_by")})
    return out


def attacked(scenario, cfg, spans):
    """Entity -> (kind, tick the attack on it began), from the answer key and the scenario."""
    out = {s: ("service", v[0][0]) for s, v in spans.items()}
    for o in scenario.get("vigie_orders", []):
        agent = o.get("agent", "trader-3")
        out[agent] = ("agent", min(o["tick"], out.get(agent, ("", o["tick"]))[1]))
    for tick, args in scenario_steps(scenario, Clock.from_config(cfg)):
        if args and args[0] == "fake-agent":
            out.setdefault(args[args.index("--as") + 1] if "--as" in args else "risk", ("forged", tick))
    return out


def vigie_record(messages):
    """What the Vigie said and did, from its per-tick vigie_state heartbeats (each holds only its latest actions)."""
    actions, held, red = {}, set(), False
    for m in messages:
        p = m["payload"]
        if m["from"] == "vigie" and p.get("kind") == "vigie_state":
            s = p["state"]
            for a in s["actions"]:
                actions.setdefault((a["tick"], a["agent"], a["action"], a["reason"]), a)
            for f in s.get("findings", []):
                if f["kind"] in NAMING_FINDINGS:
                    actions.setdefault((f["tick"], f["target"], "finding", f["kind"]), {"tick": f["tick"], "agent": f["target"], "action": "finding", "reason": f["kind"]})
            held.update(s["isolated_services"], s["quarantined"])
            red = red or s["light"] == "ROUGE"
    alerts = sum(m["from"] == "vigie" and m["payload"].get("kind") == "vigie_incident" and bool(m["payload"].get("notification")) for m in messages)
    return list(actions.values()), held, red, alerts


def ratio(a, b):
    return round(a / b, 4) if b else None


def measure(run_dir, comparison, cfg, scenario):
    """Grade one A/B replay: what got through, what the Vigie saw and held and when, what it cost."""
    ma, mb = read_journal(run_dir / "A"), read_journal(run_dir / "B")
    sa, sb = read_states(run_dir / "A"), read_states(run_dir / "B")
    spans = compromised_spans(sb)
    oa, ob = operations(ma, compromised_spans(sa)), operations(mb, spans)

    def asr(ops, world):
        bad = [o for o in ops if o["bad"]]
        won, tried = sum(o["executed"] for o in bad), len(bad) + forged(run_dir / world)
        return {"succeeded": won, "attempted": tried, "rate": ratio(won, tried) or 0.0}

    tp = sum(o["bad"] and not o["executed"] for o in ob)
    fp = sum(not o["bad"] and not o["executed"] for o in ob)
    fn = sum(o["bad"] and o["executed"] for o in ob)
    actions, held, red, alerts = vigie_record(mb)
    truth = attacked(scenario, cfg, spans)
    cve = min((t for t, args in scenario_steps(scenario, Clock.from_config(cfg)) if args and args[0] == "publish-cve"), default=None)
    entities = []
    for e, (kind, start) in sorted(truth.items()):
        named = [a for a in actions if a["agent"] == e and a["action"] != "green" and (kind != "forged" or a["reason"] in FORGED_REASONS)]
        stops = {"agent": {"quarantine"}, "service": {"block", "red"}}.get(kind)
        hold = named if stops is None else [a for a in named if a["action"] in stops and e in held]
        d, i = min((a["tick"] for a in named), default=None), min((a["tick"] for a in hold), default=None)
        # An injected event reaches the Vigie before its tick's clock message, stamped one tick early;
        # nothing is seen before the attack, except a service's compromise from its CVE on.
        floor = min(start, cve) if kind == "service" and cve is not None else start
        d, i = (None if t is None else max(t, floor) for t in (d, i))
        mine = [o for o in ob if e in o["sources"]]
        leaks = [o["tick"] for o in mine if o["executed"]]
        acted = [o["tick"] for o in mine if not o["executed"]] + ([i] if i is not None else [])
        # Contained: the first act against it after which none of its operations executes.
        c = min((t for t in acted if t > max(leaks, default=-1)), default=None)
        entities.append(
            {
                "entity": e,
                "kind": kind,
                "attacked_at": start,
                "detected_at": d,
                "isolated_at": i,
                "contained_at": c,
                "detection_ticks": None if d is None else d - start,
                "containment_ticks": None if c is None else c - start,
                "passed": len(leaks),
            }
        )
    flagged = held | {x["entity"] for x in entities if x["kind"] == "forged" and x["isolated_at"] is not None}
    agents = [*agent_ids(cfg), "executor"]
    contaminated = lambda states: sum(c["agent"] in agents for c in states[max(states)]["contaminated_agents"])
    A, B = comparison["A"], comparison["B"]
    trading = lambda m: m["pnl_net_eur"] + m["fraud_loss_eur"]
    legit = lambda ops: sum(o["executed"] and not o["bad"] for o in ops)
    return {
        "seed": comparison["seed"],
        "ticks": comparison["ticks"],
        "asr": {"without": asr(oa, "A"), "with": asr(ob, "B")},
        "gate": {"operations": len(ob), "true_blocks": tp, "false_blocks": fp, "missed": fn},
        "detection": {"entities": entities, "flagged": sorted(flagged), "true_flags": len(flagged & set(truth))},
        "money_eur": {
            "without_net": A["pnl_net_eur"],
            "with_net": B["pnl_net_eur"],
            "fraud_blocked": round(A["fraud_loss_eur"] - B["fraud_loss_eur"], 2),
            "net_saved": round(B["pnl_net_eur"] - A["pnl_net_eur"], 2),
            "cost_of_blocks": round(trading(B) - trading(A), 2),
            "frozen_clean": round(sum(o["eur"] for o in ob if not o["bad"] and not o["executed"]), 2),
        },
        "impact": {
            "agents": len(agents),
            "contaminated_without": contaminated(sa),
            "contaminated_with": contaminated(sb),
            "red_with": sorted(held & set(agents)),
            "bank_red": red,
        },
        "utility": {"legit_without": legit(oa), "legit_with": legit(ob)},
        "alerts": alerts,
        "latency_ms": comparison.get("gate_latency_ms", []),
        "timeouts": sum(o["by"] == "timeout" for o in ob),
    }


def wilson(k, n, z=1.96):
    if not n:
        return {"k": k, "n": n, "rate": None, "low": None, "high": None}
    p, d = k / n, 1 + z * z / n
    centre, half = (p + z * z / (2 * n)) / d, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return {"k": k, "n": n, "rate": round(p, 4), "low": round(max(0.0, centre - half), 4), "high": round(min(1.0, centre + half), 4)}


def interval(values, draws=10_000):
    """Mean and 95 % percentile bootstrap over seeds."""
    values = [v for v in values if v is not None]
    if not values:
        return {"mean": None, "low": None, "high": None}
    rng = random.Random(0)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(draws))
    return {"mean": round(statistics.fmean(values), 4), "low": round(means[int(0.025 * draws)], 4), "high": round(means[int(0.975 * draws) - 1], 4)}


def latency(values):
    if not values:
        return {"n": 0, "p50": None, "p99": None, "max": None}
    p99 = statistics.quantiles(values, n=100, method="inclusive")[98] if len(values) > 1 else values[0]
    return {"n": len(values), "p50": round(statistics.median(values), 2), "p99": round(p99, 2), "max": round(max(values), 2)}


def scenario_metrics(name, runs, tick_seconds):
    """One scenario over its seeds: counts are pooled, money is the mean per run."""
    pool = lambda path: sum(path(r) for r in runs)
    asr = {
        w: {
            "succeeded": pool(lambda r: r["asr"][w]["succeeded"]),
            "attempted": pool(lambda r: r["asr"][w]["attempted"]),
        }
        for w in ("without", "with")
    }
    for a in asr.values():
        a["rate"] = ratio(a["succeeded"], a["attempted"]) or 0.0
    gate = {k: pool(lambda r: r["gate"][k]) for k in ("operations", "true_blocks", "false_blocks", "missed")}
    gate["precision"] = ratio(gate["true_blocks"], gate["true_blocks"] + gate["false_blocks"])
    gate["recall"] = ratio(gate["true_blocks"], gate["true_blocks"] + gate["missed"])
    entities = [e for r in runs for e in r["detection"]["entities"]]
    flags = pool(lambda r: len(r["detection"]["flagged"]))
    delays = lambda key: [e[key] for e in entities if e[key] is not None]
    worst = lambda key: max(delays(key), default=None)
    detection = {
        "attacked": len(entities),
        "detected": len(delays("detection_ticks")),
        "contained": len(delays("containment_ticks")),
        "precision": ratio(pool(lambda r: r["detection"]["true_flags"]), flags),
        "recall": ratio(len(delays("detection_ticks")), len(entities)),
        "detection_ticks": worst("detection_ticks"),
        "containment_ticks": worst("containment_ticks"),
    }
    detection["detection_s"] = None if detection["detection_ticks"] is None else detection["detection_ticks"] * tick_seconds
    detection["containment_s"] = None if detection["containment_ticks"] is None else detection["containment_ticks"] * tick_seconds
    detection["entities"] = [{"seed": r["seed"], **e} for r in runs for e in r["detection"]["entities"]]
    money = {k: round(statistics.fmean(r["money_eur"][k] for r in runs), 2) for k in runs[0]["money_eur"]}
    first = runs[0]["impact"]
    impact = {
        "agents": first["agents"],
        "contaminated_without": max(r["impact"]["contaminated_without"] for r in runs),
        "contaminated_with": max(r["impact"]["contaminated_with"] for r in runs),
        "red_with": sorted({a for r in runs for a in r["impact"]["red_with"]}),
        "bank_red": any(r["impact"]["bank_red"] for r in runs),
    }
    utility = {k: pool(lambda r: r["utility"][k]) for k in ("legit_without", "legit_with")}
    utility["ratio"] = ratio(utility["legit_with"], utility["legit_without"])
    return {
        "scenario": name,
        "seeds": [r["seed"] for r in runs],
        "ticks": runs[0]["ticks"],
        "asr": asr,
        "gate": gate,
        "detection": detection,
        "money_eur": money,
        "impact": impact,
        "utility": utility,
        "gate_latency_ms": latency([v for r in runs for v in r["latency_ms"]]),
        "runs": [{k: r[k] for k in ("seed", "asr", "gate", "money_eur", "impact", "utility", "alerts", "timeouts")} for r in runs],
    }


def clean_metrics(runs):
    """Clean seeds: every refusal, isolation and human alert is false; utility is the legitimate work kept."""
    detail = [
        {
            "seed": r["seed"],
            "operations": r["gate"]["operations"],
            "false_blocks": r["gate"]["false_blocks"],
            "false_alerts": r["alerts"],
            "false_isolations": len(r["detection"]["flagged"]),
            "legit_without": r["utility"]["legit_without"],
            "legit_with": r["utility"]["legit_with"],
            "trading_delta_eur": r["money_eur"]["cost_of_blocks"],
        }
        for r in runs
    ]
    return {
        "scenario": CLEAN,
        "seeds": [d["seed"] for d in detail],
        "runs": len(detail),
        "false_block_rate": wilson(sum(d["false_blocks"] for d in detail), sum(d["operations"] for d in detail)),
        "runs_with_false_block": wilson(sum(d["false_blocks"] > 0 for d in detail), len(detail)),
        "runs_with_false_alert": wilson(sum(d["false_alerts"] > 0 for d in detail), len(detail)),
        "runs_with_false_isolation": wilson(sum(d["false_isolations"] > 0 for d in detail), len(detail)),
        "false_blocks_per_run": interval([d["false_blocks"] for d in detail]),
        "false_alerts_per_run": interval([d["false_alerts"] for d in detail]),
        "utility": interval([ratio(d["legit_with"], d["legit_without"]) for d in detail]),
        "trading_delta_eur": interval([d["trading_delta_eur"] for d in detail]),
        "runs_detail": detail,
    }


def metrics(measured, clean, seeds, tick_seconds):
    rows = [scenario_metrics(n, runs, tick_seconds) for n, runs in sorted(measured.items())]
    every = [r for runs in measured.values() for r in runs] + clean
    return {
        "seeds": seeds,
        "tick_seconds": tick_seconds,
        "scenarios": rows,
        "clean": clean_metrics(clean) if clean else None,
        "gate_latency_ms": {**latency([v for r in every for v in r["latency_ms"]]), "timeouts": sum(r["timeouts"] for r in every)},
        "totals": {
            "attack_runs": sum(len(runs) for runs in measured.values()),
            "attempts": sum(r["asr"]["without"]["attempted"] for runs in measured.values() for r in runs),
            "clean_runs": len(clean),
            **{f"{k}_eur": round(sum(row["money_eur"][k] for row in rows), 2) for k in ("fraud_blocked", "net_saved", "cost_of_blocks")},
        },
    }


def proposals_from(graded, table, thresholds, proofs):
    out = []
    for rule, rows in table.items():
        now = next(r for r in rows if r["value"] == thresholds[rule])
        pick = best(rows, thresholds[rule])
        if pick["value"] == thresholds[rule]:
            continue
        item = {
            "kind": "threshold",
            "target": rule,
            "current": thresholds[rule],
            "proposed": pick["value"],
            "why": (
                f"{rule} at {pick['value']} catches {len(pick['caught'])} compromised services (now {len(now['caught'])})"
                if rule in BRAKE_RULES
                else f"{rule} at {pick['value']} warns on {len(pick['caught'])} limits after an attack began (now {len(now['caught'])})"
            )
            + f", with {pick['false_alerts']} false alerts (now {now['false_alerts']}).",
            "evidence": {"sweep": rows},
            "apply": f"human set_threshold {rule} {pick['value']}",
        }
        proof = proofs.get(rule)
        if proof:
            item["replay"] = proof
            item["why"] += f" Replayed on every scenario: avoided losses move by {proof['delta']['avoided_loss_eur']:+,.0f} EUR, frozen money by {proof['delta']['frozen_eur']:+,.0f} EUR."
            item["proven"] = proof["delta"]["avoided_loss_eur"] >= 0
        else:
            item["why"] += " This rule only warns a human: it does not move the brake, so no money changes."
            item["proven"] = True
        out.append(item)
    for g in graded:
        if g["missed"]:
            first = g["missed"][0]
            fed = sorted({s for m in g["missed"] for s in m["compromised_sources"] or []})
            worth = sum(m["amount_eur"] or 0 for m in g["missed"])
            caught = [d for d in g["detection"] if d["detected_at"] is not None]
            out.append(
                {
                    "kind": "gap",
                    "target": g["scenario"],
                    "why": f"{len(g['missed'])} contaminated operations worth {worth:,.0f} EUR still passed in {g['scenario']} (first at tick {first['tick']}, {first['kind']} by {first['agent']}), fed by {', '.join(fed) or 'no service'}; no layer refused them."
                    + ("" if caught else " No indicator flagged the service in time."),
                    "evidence": {"missed": g["missed"][:5], "detection": g["detection"]},
                }
            )
        explained = [a for a in g["indicator_alerts"] if a["rule"] == "price_drift_z" and not a["true"] and a["tick"] in set(g["news_ticks"])]
        if explained:
            out.append(
                {
                    "kind": "rule",
                    "target": "price_drift_z",
                    "why": f"In {g['scenario']}, {len(explained)} price-drift alerts fell on ticks with a real news item ({', '.join(str(a['tick']) for a in explained)}): cross-check a price jump with the news feed before marking market-feed suspect.",
                    "evidence": {"alerts": explained},
                }
            )
    for rule, r in sorted({k: v for g in graded for k, v in g["refusals"].items()}.items()):
        if r["clean"] and not r["contaminated"] and rule.startswith("vigie:"):
            out.append({"kind": "rule", "target": rule, "why": f"'{rule}' refused {r['clean']} clean operations ({r['frozen_eur']:,.0f} EUR) and no contaminated one: check whether it is too strict.", "evidence": r})
    return out


def run(scenario_names=None, out_root=None, workers=4, seeds=(), clean_seeds=CLEAN_SEEDS):
    """`seeds`: extra seeds for the attack scenarios' metrics; the config seed is always one of them."""
    cfg = load_config(overrides={"llm": {"enabled": False, "cache": False}})
    thresholds = {**THRESHOLDS, **cfg["vigie"].get("thresholds", {})}
    paths = {load_scenario(p)["name"]: p for p in scenario_paths()}
    names = scenario_names or sorted(paths)
    scenarios = {n: load_scenario(paths[n]) for n in {*names, CLEAN}}
    root = Path(out_root or Path(cfg["runs_dir"]) / "vigie-improve")
    work = root / time.strftime("%Y%m%d-%H%M%S")
    work.mkdir(parents=True)
    measured_seeds = {cfg["seed"], *seeds, *clean_seeds}
    if CALIBRATION_SEED in measured_seeds:
        raise ValueError(f"calibration seed {CALIBRATION_SEED} is also a measured seed")
    profiles = work / "profiles.json"
    profiles.write_text(canonical(learn_reference({**cfg, "seed": CALIBRATION_SEED}, 300, work / "calibration")))

    def batch(label, overrides, jobs=None):
        jobs = jobs or {n: (n, None) for n in names}
        with ThreadPoolExecutor(workers) as pool:
            futures = {key: pool.submit(compare, paths[n], work / label / key, overrides, work, seed, profiles) for key, (n, seed) in jobs.items()}
            return {key: f.result() for key, f in futures.items()}

    (work / "baseline").mkdir()
    base = batch("baseline", thresholds)
    graded = [grade_run(n, base[n], work / "baseline" / n, cfg, scenarios[n], thresholds) for n in names]
    attack_names = [n for n in names if n != CLEAN]
    extra = {f"{n}-s{s}": (n, s) for n in attack_names for s in seeds if s != cfg["seed"]}
    extra |= {f"{CLEAN}-s{s}": (CLEAN, s) for s in clean_seeds}
    seeded = batch("seeds", thresholds, extra) if extra else {}
    runs = {n: work / "baseline" / n for n in names}
    # The clean seeds count the false alerts a threshold would raise on ordinary days.
    clean_runs = {key: work / "seeds" / key for key, (n, _) in extra.items() if n == CLEAN}
    table = sweep(cfg, runs | clean_runs, scenarios | {key: scenarios[CLEAN] for key in clean_runs}, thresholds)
    proofs = {}
    for rule in BRAKE_RULES:
        pick = best(table[rule], thresholds[rule])
        if pick["value"] == thresholds[rule]:
            continue
        label = f"{rule}-{pick['value']}"
        (work / label).mkdir()
        tried = batch(label, {**thresholds, rule: pick["value"]})
        regraded = [grade_run(n, tried[n], work / label / n, cfg, scenarios[n], {**thresholds, rule: pick["value"]}) for n in names]
        before, after = totals(graded), totals(regraded)
        proofs[rule] = {
            "before": before,
            "after": after,
            "delta": {k: round(after[k] - before[k], 2) for k in before},
            "by_scenario": {g["scenario"]: {"avoided_loss_eur": g["avoided_loss_eur"], "frozen_eur": g["frozen_eur"]} for g in regraded},
        }
    measured = {n: [measure(work / "baseline" / n, base[n], cfg, scenarios[n])] for n in attack_names}
    clean = []
    for key, (n, _) in extra.items():
        (clean if n == CLEAN else measured[n]).append(measure(work / "seeds" / key, seeded[key], cfg, scenarios[n]))
    seed_plan = {"calibration": CALIBRATION_SEED, "attack": sorted({cfg["seed"], *seeds}), "clean": sorted(clean_seeds)}
    proposals = proposals_from(graded, table, thresholds, proofs)
    for i, p in enumerate(proposals, 1):
        p["id"] = i
    rules = defaultdict(lambda: {"fired": 0, "true": 0, "false": 0, "blocked_eur": 0.0, "frozen_eur": 0.0, "layer": "brake"})
    for g in graded:
        for name, r in g["refusals"].items():
            rules[name].update(
                fired=rules[name]["fired"] + r["count"],
                true=rules[name]["true"] + r["contaminated"],
                false=rules[name]["false"] + r["clean"],
                blocked_eur=round(rules[name]["blocked_eur"] + r["blocked_eur"], 2),
                frozen_eur=round(rules[name]["frozen_eur"] + r["frozen_eur"], 2),
            )
        for a in g["indicator_alerts"]:
            rules[a["rule"]]["layer"] = "metrics"
            rules[a["rule"]]["fired"] += 1
            rules[a["rule"]]["true" if a["true"] else "false"] += 1
    total = totals(graded)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "thresholds": thresholds,
        "summary": [
            f"{len(names)} scenarios replayed with and without the Vigie.",
            f"Losses avoided: {total['avoided_loss_eur']:,.0f} EUR; cost of the brake on trading: {total['brake_cost_eur']:,.0f} EUR; clean operations frozen: {total['frozen_eur']:,.0f} EUR.",
            f"Contaminated operations that still passed: {total['missed_operations']}.",
            f"{sum(p['kind'] == 'threshold' for p in proposals)} threshold changes and {sum(p['kind'] != 'threshold' for p in proposals)} rule or coverage gaps for a human to review.",
        ],
        "totals": total,
        "scenarios": graded,
        "rules": [{"rule": k, **v} for k, v in sorted(rules.items())],
        "sweep": table,
        "proposals": proposals,
        "metrics": metrics(measured, clean, seed_plan, Clock.from_config(cfg).period),
        "evidence_dir": str(work),
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    (work / "improvements.json").write_text(text, encoding="utf-8")
    (root / "improvements.json").write_text(text, encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenarios", help="comma-separated scenario names (default: all)")
    parser.add_argument("--out")
    args = parser.parse_args()
    report = run(args.scenarios.split(",") if args.scenarios else None, args.out)
    print("\n".join(report["summary"]))
    for p in report["proposals"]:
        print(f"#{p['id']} [{p['kind']}] {p['why']}")
    print(f"{report['evidence_dir']}/improvements.json")


if __name__ == "__main__":
    main()
