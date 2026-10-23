"""Étape 1 : horloge, bus, journal, signatures."""

import json
import threading
import time

import fakeredis
import pytest

from bus.memory_bus import MemoryBus
from bus.message import Keyring, MessageFactory, merge_provenance, matches
from bus.redis_bus import RedisBus
from journal.writer import JournalWriter, read_journal, verify_journal
from sim.clock import Clock, parse_at
from sim.config import known_senders


@pytest.fixture
def keyring(cfg):
    return Keyring(known_senders(cfg))


def test_signed_message_verifies(keyring):
    msg = MessageFactory("risk", keyring).make(3, "risk_decision", "executor", {"approved": True})
    assert keyring.verify(msg)


def test_tampered_payload_is_detected(keyring):
    msg = MessageFactory("risk", keyring).make(3, "risk_decision", "executor", {"approved": False, "notional": 1e6})
    msg["payload"]["approved"] = True
    assert not keyring.verify(msg)


def test_tampered_provenance_is_detected(keyring):
    prov = [{"service": "market-feed", "version": "3.2.0", "response_hash": "abc", "tick": 1}]
    msg = MessageFactory("quant", keyring).make(1, "price_update", "*", {}, data_provenance=prov)
    msg["data_provenance"] = []
    assert not keyring.verify(msg)


def test_unknown_sender_is_rejected(keyring):
    fake = MessageFactory("trader-9").make(5, "order_proposal", "compliance", {"notional": 5e6})
    keyring.sign(fake, key=b"cle-inconnue")
    assert not keyring.verify(fake)


def test_known_sender_with_wrong_key_is_rejected(keyring):
    fake = MessageFactory("risk").make(5, "risk_decision", "executor", {"approved": True})
    keyring.sign(fake, key=b"pas-la-bonne-cle")
    assert not keyring.verify(fake)


def test_missing_signature_is_rejected(keyring):
    msg = MessageFactory("quant").make(1, "heartbeat", "*")
    assert msg["signature"] is None
    assert not keyring.verify(msg)


def test_unknown_message_type_is_refused():
    with pytest.raises(ValueError):
        MessageFactory("quant").make(1, "shell_exec", "*")


def test_deterministic_ids():
    a = [MessageFactory("quant").make(t, "heartbeat", "*")["message_id"] for t in range(3)]
    b = [MessageFactory("quant").make(t, "heartbeat", "*")["message_id"] for t in range(3)]
    assert a == b


def test_provenance_merge_dedupes_and_keeps_order():
    p1 = {"service": "market-feed", "version": "1", "response_hash": "h1", "tick": 1}
    p2 = {"service": "news-wire", "version": "1", "response_hash": "h2", "tick": 2}
    assert merge_provenance([p1], [p2, p1]) == [p1, p2]


def test_routing():
    msg = {"to": "traders", "type": "risk_decision"}
    assert matches(msg, "trader-2", "trader")
    assert not matches(msg, "risk", "risk")
    assert matches({"to": "compliance", "type": "order_proposal"}, "trader-1", "trader", observes={"order_proposal"})


def test_journal_chain_and_tamper_detection(tmp_path, keyring):
    path = tmp_path / "journal.jsonl"
    writer = JournalWriter(path)
    factory = MessageFactory("quant", keyring)
    for t in range(5):
        writer.append(factory.make(t, "heartbeat", "*"), True)
    writer.close()
    assert verify_journal(path) == (True, 5, None)

    lines = path.read_text().splitlines()
    entry = json.loads(lines[2])
    entry["message"]["tick"] = 99
    lines[2] = json.dumps(entry)
    path.write_text("\n".join(lines) + "\n")
    ok, _, bad = verify_journal(path)
    assert not ok and bad == 3


def test_journal_deleted_line_detected(tmp_path, keyring):
    path = tmp_path / "journal.jsonl"
    writer = JournalWriter(path)
    for t in range(4):
        writer.append(MessageFactory("quant", keyring).make(t, "heartbeat", "*"), True)
    writer.close()
    lines = path.read_text().splitlines()
    del lines[1]
    path.write_text("\n".join(lines) + "\n")
    assert not verify_journal(path)[0]


def test_journal_resumes_chain(tmp_path, keyring):
    path = tmp_path / "journal.jsonl"
    w = JournalWriter(path)
    w.append(MessageFactory("quant", keyring).make(0, "heartbeat", "*"), True)
    w.close()
    w = JournalWriter(path)
    w.append(MessageFactory("risk", keyring).make(1, "heartbeat", "*"), False)
    w.close()
    assert verify_journal(path) == (True, 2, None)
    assert [e["signature_valid"] for e in read_journal(path)] == [True, False]


def test_clock_conversions():
    clock = Clock(tick_seconds=1.0, accelerator=4.0, ticks_per_day=300)
    assert clock.period == 0.25
    assert parse_at("30s", clock) == 30
    assert parse_at("2min", clock) == 120
    assert parse_at(45, clock) == 45
    assert clock.day_of(299) == 0 and clock.day_of(300) == 1


def test_clock_runs_ticks_in_order():
    clock = Clock(tick_seconds=0.01, accelerator=1.0)
    seen = []
    clock.run(seen.append, max_ticks=5)
    assert seen == [0, 1, 2, 3, 4]


def test_memory_bus_fifo_and_taps():
    bus, tapped = MemoryBus(), []
    bus.tap(tapped.append)
    bus.publish({"n": 1})
    bus.publish({"n": 2})
    assert [bus.pop()["n"], bus.pop()["n"], bus.pop()] == [1, 2, None]
    assert len(tapped) == 2


def test_redis_bus_roundtrip(keyring):
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    bus = RedisBus(client, prefix="A:")
    received = []

    def listen():
        for msg in bus.listen(timeout=0.05):
            if msg is not None:
                received.append(msg)
                return

    thread = threading.Thread(target=listen)
    thread.start()
    time.sleep(0.2)
    msg = MessageFactory("quant", keyring).make(1, "heartbeat", "*")
    bus.publish(msg)
    thread.join(timeout=2)
    assert received and keyring.verify(received[0])
