import json

from vigie.auditor import Auditor


def test_auditor_only_raises_and_ignores_bad_advice(setup):
    c, _, _ = setup
    asked = []
    answer = {"raise": [
        {"agent": "trader-1", "delta": 15, "reason": "orders keep growing"},
        {"agent": "trader-2", "delta": -50, "reason": "lower it"},
        {"agent": "trader-3", "delta": 90, "reason": "too much"},
        {"agent": "nobody", "delta": 5, "reason": "unknown"},
    ]}
    a = Auditor(c, ask=lambda text: asked.append(text) or json.dumps(answer))
    a.run(a.brief(), c.tick)
    a.apply(a.pending.popleft())
    assert c.suspicion["trader-1"] == 15
    assert c.suspicion["trader-2"] == 0 and c.suspicion["trader-3"] == 0
    assert c.actions[-1]["layer"] == "auditor" and len(a.log) == 1
    assert "never the agents' own words" in asked[0]


def test_auditor_is_off_without_a_key(setup, monkeypatch):
    c, _, _ = setup
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    a = Auditor(c)
    a.step()
    assert not c.auditor_enabled and not a.busy
