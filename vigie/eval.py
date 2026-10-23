"""Évaluation Dev 2 : l'oracle n'est utilisé qu'ici pour noter les détecteurs."""
import argparse
import copy
from collections import Counter
import shlex
from dataclasses import asdict
import json
from pathlib import Path
import time

from sim.config import load_config
from sim.inject import load_scenario, schedule_scenario
from sim.lockstep import Lockstep
from vigie.main import Runtime, couche3_config

SCENARIOS = ("php2shell_day", "ledger_legacy", "rumeur_mimetisme", "trader_corrompu_49m", "clean")


def _truth(ls, msg, labels):
    # Réservé à la notation : cet oracle n'est jamais transmis à Runtime.
    direct = any(ls.store.sismember(ls.prefix + "tainted", e["response_hash"])
                 for e in msg.get("data_provenance", []) if e.get("response_hash"))
    return direct or any(labels.get(parent, False) for parent in msg.get("depends_on", []))


def _scores(predictions, labels):
    tp = sum(value and labels.get(id_, False) for id_, value in predictions.items())
    fp = sum(value and not labels.get(id_, False) for id_, value in predictions.items())
    fn = sum(value and not predictions.get(id_, False) for id_, value in labels.items())
    return {"true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None}


def schedule_evaluation(ls, scenario):
    """Injection de démonstration, sans importer l'ancien contrôleur Vigie."""
    from bus.message import MessageFactory
    from sim.clock import Clock, parse_at
    scenario = copy.deepcopy(scenario)
    injections, regular = [], []
    for step in scenario.get("steps", []):
        args = shlex.split(step.get("do", ""))
        if args and args[0] == "corrupt-order":
            if len(args) != 6:
                raise ValueError("corrupt-order attend agent, actif, sens, montant et justification")
            injections.append((parse_at(step["at"], Clock.from_config(ls.cfg)), args))
        else:
            regular.append(step)
    scenario["steps"] = regular
    duration = schedule_scenario(ls, scenario)
    injected = set()
    if not injections:
        return duration, injected
    latest_quant = {}
    factories = {}

    def observe(msg):
        if msg.get("from") == "quant" and msg.get("type") == "price_update" and ls.keyring.verify(msg):
            latest_quant.clear()
            latest_quant.update(msg)
    ls.bus.tap(observe)

    def inject(world, args):
        _, agent, asset, side, amount, *words = args
        # Le prix et la provenance sont ceux du dernier message Quant observable,
        # jamais ceux du marché caché du simulateur.
        if not latest_quant or asset not in latest_quant["payload"]["assets"]:
            raise ValueError("Référence Quant absente pour l'injection")
        factory = factories.setdefault(agent, MessageFactory(agent, world.keyring))
        factory.nonce = "corrupted:"
        msg = factory.make(world.tick, "order_proposal", "compliance", {
            "proposal_id": f"corrupted-{world.tick}", "trader": agent, "profile": "news",
            "asset": asset, "side": side, "notional_eur": float(amount),
            "price_seen": latest_quant["payload"]["assets"][asset]["price"],
            "quant_confidence": 1.0,
            "signals": {"own": -1.0, "peer": 0.0, "herding": 0.0, "combined": -1.0},
            "news_refs": [{"id": "forum-piege", "source": "forum anonyme", "reliability": 0.05, "confidential": False}],
            "justification": " ".join(words)}, depends_on=[latest_quant["message_id"]],
            data_provenance=latest_quant["data_provenance"])
        injected.add(msg["message_id"])
        world.publish(msg)

    for tick, args in injections:
        ls.at(tick, lambda world, args=args: inject(world, args))
    return duration, injected


def play(cfg, scenario, ticks, enabled, *, capture_frames=False):
    cfg = couche3_config(cfg)
    # L'injection du portique reste locale à l'évaluation ; le moteur de l'équipe
    # et son comportement par défaut ne sont pas modifiés.
    cfg["gate"]["impl"] = "stub"
    ls = Lockstep(cfg, no_llm=True, no_vigie=not enabled)
    meta = {sid: core.meta() for sid, core in ls.cores.items()}
    runtime = Runtime(cfg, ls.keyring, meta=meta)
    runtime.freeze.record = capture_frames
    ls.vigie = None
    if enabled:
        from bus.gate import Decision
        class LocalGate:
            def request_approval(self, msg):
                if "vigie" in ls.killed:
                    return Decision(False, "Vigie débranchée", "timeout")
                return runtime.request_approval(msg)
        ls.gate = ls.world.gate = ls.world.executor.gate = LocalGate()
    messages, labels, online, injected_labels = {}, {}, {}, {}
    injected_ids = set()

    def observe(msg):
        if msg.get("from") == "vigie":
            return
        labels[msg["message_id"]] = _truth(ls, msg, labels)
        injected_labels[msg["message_id"]] = (msg["message_id"] in injected_ids
            or any(injected_labels.get(parent, False) for parent in msg.get("depends_on", [])))
        messages[msg["message_id"]] = msg
        if enabled and "vigie" in ls.killed:
            online[msg["message_id"]] = False
            return
        out = runtime.on_message(msg)
        online[msg["message_id"]] = runtime.contagion.marked(msg)[0]
        if enabled:
            for emitted in out:
                ls.publish(emitted)

    ls.bus.tap(observe)
    duration, injected_ids = schedule_evaluation(ls, scenario)
    try:
        state = ls.run(ticks or duration)
        predictions = {id_: id_ in runtime.state.messages and runtime.contagion.marked(msg)[0] for id_, msg in messages.items()}
        attacks = {}
        for tick, args, _ in ls.scenario_log:
            if args[0] == "compromise":
                attacks.setdefault(args[1], tick)
        detected = {sid: {**asdict(f), "attack_tick": attacks.get(sid),
            "delay_from_attack_ticks": f.tick - attacks[sid] if sid in attacks else None}
            for sid, f in sorted(runtime.contagion.suspects.items())}
        return {"pnl_net_eur": round(state["pnl"]["trading_eur"] + state["pnl"]["payment_fraud_eur"], 2),
                "fraud_loss_eur": -state["pnl"]["payment_fraud_eur"],
                "trapped_operations": len(state["trapped_operations"]),
                "ground_truth_agents": sorted(ls.world.observer.contaminated),
                "marking": _scores(predictions, labels), "online_marking": _scores(online, labels),
                "detections": detected, "undetected_attacks": sorted(set(attacks) - set(detected)),
                "cve_seen": bool(runtime.contagion.cve_ticks),
                "blocked_operations": len(runtime.contagion.blocked),
                "freeze_starts": sum(t["event"] == "start" for t in runtime.freeze.transitions),
                "freeze_blocked_operations": len(runtime.freeze.blocked),
                "freeze_transitions": list(runtime.freeze.transitions),
                "freeze_active": copy.deepcopy(runtime.freeze.active),
                "freeze_uncertain": sorted(runtime.freeze.uncertain),
                "unanalysed_operations": sum(d.get("unanalysed",False) for d in runtime.decisions),
                "input_rejections": {source:dict(counts) for source,counts in runtime.admission.rejected.items()},
                "freeze_guard_refusals": sum(d.get('freeze_guard',False) for d in runtime.decisions),
                **({"freeze_frames": list(runtime.freeze.frames)} if capture_frames else {}),
                "injected_orders": len(injected_ids),
                "injected_blocked_operations": sum(not d["approved"] and injected_labels.get(d["message_id"], False)
                    for d in runtime.decisions),
                "injected_executions": sum(m["type"] == "execution" and m["payload"].get("status") == "executed"
                    and injected_labels.get(id_, False) for id_, m in messages.items()),
                "herding_finding_counts": dict(Counter(f.kind for f in runtime.findings if f.target == "traders")),
                "metrics": runtime.contagion.metrics(),
                "herding": runtime.analytics.snapshot()["herding"],
                "contagion_r0": runtime.analytics.snapshot()["contagion"],
                "herding_findings": [asdict(f) for f in runtime.analytics.herding.findings(runtime.state.tick)]}
    finally:
        ls.close()


def evaluate(name, ticks=None, seed=42, *, freeze_profile="default"):
    if name not in SCENARIOS:
        raise ValueError(f"Scénario hors périmètre couche 3 : {name}")
    started = time.perf_counter()
    cfg = load_config(overrides={"seed": seed, "llm": {"enabled": False, "cache": False}})
    if freeze_profile != "default":
        cfg["vigie"]["freeze_profile"] = freeze_profile
    if name == "clean":
        scenario = {"name": "clean", "duration": "180s", "steps": []}
    else:
        directory = Path(__file__).resolve().parent if name == "trader_corrompu_49m" else Path(__file__).resolve().parents[1]
        scenario = load_scenario(directory / "scenarios" / f"{name}.yaml")
    without = play(cfg, scenario, ticks, False)
    protected = play(cfg, scenario, ticks, True)
    return {"scenario": name, "seed": seed, "ticks": ticks or int(scenario["duration"].removesuffix("s")),
            "sans_vigie": without, "avec_couche3": protected,
            "avoided_loss_eur": round(protected["pnl_net_eur"] - without["pnl_net_eur"], 2),
            "avoided_fraud_eur": round(without["fraud_loss_eur"] - protected["fraud_loss_eur"], 2),
            "duration_s": round(time.perf_counter() - started, 3)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    parser.add_argument("--ticks", type=int)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    # La démonstration n'appelle aucun réseau, même si le LLM est configuré.
    import sys
    from vigie.testing import deny_network
    sys.addaudithook(deny_network)
    results = [evaluate(name, args.ticks, args.seed) for name in args.scenario or SCENARIOS]
    rendered = json.dumps(results, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
