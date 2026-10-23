import json
import threading
import time

import fakeredis

from bus.gate import BusGate
from bus.message import MessageFactory, canonical
from vigie.service import serve
from vigie.tests.test_core import chain


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError("Vigie sans réponse")


def test_bus_gate_prefix_signatures_heartbeat_and_disconnect(cfg, setup):
    c, keys, state = setup
    prefix = "proof:"
    cfg["redis"]["prefix"] = prefix
    client = fakeredis.FakeRedis(decode_responses=True)
    client.set(prefix + "state", canonical(state))
    stop = threading.Event()
    thread = threading.Thread(target=serve, args=(cfg, client, stop), daemon=True)
    thread.start()
    try:
        wait_for(lambda: client.get(prefix + "vigie:ready"))
        sub = client.pubsub(ignore_subscribe_messages=True)
        sub.subscribe(prefix + "bus")
        tick = MessageFactory("clock", keys).make(10, "tick", "*", {"tick": 10})
        client.publish(prefix + "bus", canonical(tick))
        wait_for(lambda: client.get(prefix + "vigie:processed") == tick["message_id"])
        heard = []
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            raw = sub.get_message(timeout=0.01)
            if raw:
                heard.append(json.loads(raw["data"]))
            if any(m["type"] == "light_change" for m in heard):
                break
        assert any(
            m["type"] == "heartbeat"
            and m["from"] == "vigie"
            and keys.verify(m)
            and not m["payload"]["stub"]
            for m in heard
        )
        assert any(
            m["type"] == "light_change"
            and m["payload"]["expires_at_tick"] > 10
            and keys.verify(m)
            for m in heard
        )
        msg = chain(c, keys)
        for m in c.seen.values():
            client.publish(prefix + "bus", canonical(m))
        gate = BusGate(client, prefix, 1, "reject")
        assert gate.request_approval(msg).approved
        assert (
            not BusGate(client, "wrong:", 0.02, "reject").request_approval(msg).approved
        )
        sub.close()
    finally:
        stop.set()
        thread.join(2)
    assert not thread.is_alive()
    assert not gate.request_approval(msg).approved


def test_restart_cannot_clear_red_quarantine_or_replay(cfg, setup):
    _, keys, state = setup
    cfg["redis"]["prefix"] = "restore:"
    client = fakeredis.FakeRedis(decode_responses=True)
    client.set("restore:state", canonical(state))
    client.set(
        "restore:vigie:status",
        canonical(
            {
                "light": "ROUGE",
                "quarantined": ["trader-1"],
                "suspicion": {"trader-1": 80},
            }
        ),
    )
    client.sadd("restore:vigie:approved_proposals", "old-proposal")
    stop = threading.Event()
    worker = threading.Thread(target=serve, args=(cfg, client, stop), daemon=True)
    worker.start()
    try:
        wait_for(lambda: client.get("restore:vigie:ready"))
        msg = MessageFactory("clock", keys).make(10, "tick", "*", {"tick": 10})
        client.publish("restore:bus", canonical(msg))
        wait_for(lambda: client.get("restore:vigie:ack:" + msg["message_id"]))
        status = json.loads(client.get("restore:vigie:status"))
        assert status["light"] == "ROUGE" and status["quarantined"] == ["trader-1"]
        assert status["registry"]["trader-1"]["key_revoked"]
        assert not status["registry"]["trader-1"]["tools"]
        assert client.sismember("restore:vigie:approved_proposals", "old-proposal")
    finally:
        stop.set()
        worker.join(2)
    assert not worker.is_alive()


def test_dashboard_snapshot_follows_existing_stream_contract(cfg, setup):
    _, keys, state = setup
    cfg["redis"]["prefix"] = "display:"
    client = fakeredis.FakeRedis(decode_responses=True)
    client.set("display:state", canonical(state))
    stop = threading.Event()
    worker = threading.Thread(target=serve, args=(cfg, client, stop), daemon=True)
    sub = client.pubsub(ignore_subscribe_messages=True)
    sub.subscribe("display:bus")
    worker.start()
    try:
        wait_for(lambda: client.get("display:vigie:ready"))
        clock = MessageFactory("clock", keys).make(10, "tick", "*", {"tick": 10})
        client.publish("display:bus", canonical(clock))
        frames = []

        def snapshot():
            raw = sub.get_message(timeout=0.01)
            if raw:
                m = json.loads(raw["data"])
                if m.get("payload", {}).get("kind") == "vigie_state":
                    frames.append(m)
                    return True
            return False

        wait_for(snapshot)
        frame = frames[0]
        assert (
            keys.verify(frame)
            and frame["type"] == "heartbeat"
            and frame["to"] == "dashboard"
        )
        assert frame["payload"]["notification"] is False
        public = frame["payload"]["state"]
        assert public["schema_version"] == 1
        assert len(public["agents"]["trader-1"]["lights"]) == 4
        assert set(public["counters"]) == {"raw_alerts", "incidents", "notifications"}
        assert set(public["contamination_graph"]) == {"nodes", "edges"}
        assert "signature" not in public["registry"]["trader-1"]
    finally:
        stop.set()
        worker.join(2)
        sub.close()
