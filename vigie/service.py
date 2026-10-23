"""Un seul observateur/portique, dans un processus indépendant des agents."""

import json
import os

from bus.message import Keyring, canonical
from bus.redis_bus import next_message
from sim.config import known_senders
from sim.params import Params
from vigie.core import Controller
from vigie.state import VigieState


def serve(cfg, client, stop=None, profiles=None):
    prefix = cfg["redis"]["prefix"]
    controller = Controller(
        cfg, Keyring(known_senders(cfg)), profiles, deterministic=False
    )
    previous = json.loads(client.get(prefix + "vigie:status") or "{}")
    controller.light = previous.get("light", "VERT")
    saved_policy = client.get(prefix + "vigie:policy")
    if saved_policy:
        controller.policy.restore(json.loads(saved_policy))
    controller.tick = previous.get("tick", 0)
    controller.policy.last_tick = controller.tick
    controller.suspicion.update(previous.get("suspicion", {}))
    controller.policy.layers.update(previous.get("indicators", {}))
    controller.policy.levels.update(previous.get("levels", {}))
    controller.isolated_services.update(previous.get("isolated_services", []))
    for sid in controller.isolated_services:
        controller.causality.isolate(sid, previous.get("tick", 0))
    controller.approved_proposals.update(
        client.smembers(prefix + "vigie:approved_proposals")
    )
    for agent in previous.get("quarantined", []):
        if agent in controller.registry:
            controller.quarantined.add(agent)
            controller.registry[agent]["tools"] = set()
    saved_view = client.get(prefix + "vigie:view")
    view = VigieState(
        cfg, Params(client, prefix, cfg), json.loads(saved_view) if saved_view else None
    )
    pubsub = client.pubsub(ignore_subscribe_messages=False)
    pubsub.subscribe(prefix + "bus", prefix + "vigie.approval_requests")
    # L'acquittement de souscription précède ready : aucune première requête perdue.
    import time

    deadline = time.monotonic() + 5
    subscriptions = 0
    while subscriptions < 2:
        raw = pubsub.get_message(ignore_subscribe_messages=False, timeout=0.1)
        if raw and raw["type"] == "subscribe":
            subscriptions += 1
        if time.monotonic() > deadline:
            pubsub.close()
            raise TimeoutError("Souscriptions du portique non confirmées")
    client.set(prefix + "agent:vigie:pid", os.getpid())
    client.set(prefix + "vigie:ready", "1")
    try:
        while stop is None or not stop.is_set():
            raw = next_message(pubsub, 0.1, ignore_subscribe_messages=True)
            if raw is None:
                continue
            data = None
            try:
                data = json.loads(raw["data"])
                if raw["channel"] == prefix + "bus":
                    if isinstance(data, dict) and data.get("from") == "vigie":
                        continue
                    # The controller judges each message on the view built from the ones before it.
                    controller.observe(data, view.read())
                    view.observe(
                        data,
                        controller.keyring,
                        controller.isolated_services
                        | controller.quarantined
                        | set(controller.detect.suspect),
                    )
                    if data.get("from") == "clock":
                        controller.history.append(dict(view.prices))
                        # /graph's call counters only: the network telemetry layer 1 may read.
                        controller.layer1.topology(json.loads(client.get(prefix + "graph") or "{}"))
                    if data.get("type") in {"tick", "execution", "service_status", "payment_proposal"}:
                        client.set(prefix + "vigie:view", json.dumps(view.checkpoint()))
                else:
                    decision = controller.approve(data["message"], view.read())
                    if decision.approved:
                        client.sadd(
                            prefix + "vigie:approved_proposals",
                            data["message"]["payload"]["proposal"]["message_id"],
                        )
                    client.publish(
                        prefix + "vigie.approval_responses",
                        canonical(
                            {
                                "request_id": data["request_id"],
                                "approved": decision.approved,
                                "reason": decision.reason,
                                "decided_by": decision.decided_by,
                            }
                        ),
                    )
                    client.rpush(
                        prefix + "vigie:decisions",
                        canonical(
                            {
                                "tick": controller.tick,
                                "request_id": data["request_id"],
                                **decision.__dict__,
                            }
                        ),
                    )
            except (ValueError, KeyError, TypeError, AttributeError):
                controller.summary("bus", "block", "INVALID_ENVELOPE", 1)
            for out in controller.drain():
                client.publish(prefix + "bus", canonical(out))
            if (
                raw["channel"] == prefix + "vigie.approval_requests"
                or isinstance(data, dict)
                and data.get("from") == "clock"
            ):
                status = controller.status()
                client.set(prefix + "vigie:status", canonical(status))
                client.set(
                    prefix + "vigie:policy", canonical(controller.policy.checkpoint())
                )
                if isinstance(data, dict) and data.get("from") == "clock":
                    snapshot = controller.factory.make(
                        controller.tick,
                        "heartbeat",
                        "dashboard",
                        {
                            "agent": "vigie",
                            "role": "vigie",
                            "stub": False,
                            "kind": "vigie_state",
                            "notification": False,
                            "state": status,
                        },
                    )
                    client.publish(prefix + "bus", canonical(snapshot))
                    summary = {
                        k: status[k]
                        for k in (
                            "schema_version",
                            "tick",
                            "light",
                            "agents",
                            "counters",
                            "isolated_services",
                        )
                    }
                    client.publish(
                        prefix + "bus",
                        canonical(
                            controller.factory.make(
                                controller.tick,
                                "alert",
                                "dashboard",
                                {
                                    "kind": "vigie_summary",
                                    "notification": False,
                                    "state": summary,
                                },
                            )
                        ),
                    )

            if (
                isinstance(data, dict)
                and data.get("message_id")
                and data.get("from") != "vigie"
            ):
                client.set(prefix + "vigie:processed", data["message_id"])
                client.set(prefix + "vigie:ack:" + data["message_id"], "1", ex=60)
    finally:
        pubsub.close()
        client.delete(prefix + "vigie:ready")


def main():
    import argparse
    from pathlib import Path
    from bus.redis_bus import connect
    from sim.config import load_config

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--redis-url")
    parser.add_argument("--prefix")
    parser.add_argument("--profiles")
    args = parser.parse_args()
    cfg = load_config(overrides={"llm": {"enabled": False}})
    if args.redis_url:
        cfg["redis"]["url"] = args.redis_url
    if args.prefix is not None:
        cfg["redis"]["prefix"] = args.prefix
    profile_path = args.profiles or cfg["vigie"].get("profiles_path")
    profiles = json.loads(Path(profile_path).read_text()) if profile_path else None
    print("[vigie] portique Redis actif ; auditeur LLM désactivé", flush=True)
    serve(cfg, connect(cfg["redis"]["url"]), profiles=profiles)
