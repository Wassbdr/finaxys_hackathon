"""Étape 5 : commandes d'injection et scénarios YAML."""

import subprocess
from pathlib import Path

import fakeredis
import pytest

from sim.inject import LockstepTarget, ProcessTarget, load_scenario, run_command, schedule_scenario
from sim.lockstep import Lockstep

SCENARIOS = Path(__file__).resolve().parent.parent / "scenarios"


@pytest.fixture
def ls(cfg):
    world = Lockstep(cfg)
    world.run(3)
    return world


def cmd(ls, line):
    import shlex

    out = run_command(LockstepTarget(ls), shlex.split(line))
    ls.drain()
    return out


@pytest.mark.parametrize("verb,mode", [("compromise", "compromised"), ("stale", "stale"), ("down", "down"),
                                       ("slow", "slow"), ("restore", "normal")])
def test_service_mode_commands(ls, verb, mode):
    cmd(ls, f"{verb} core-ledger")
    assert ls.cores["core-ledger"].mode() == mode
    announced = [s["mode"] for s in ls.world.observer.service_timeline]
    assert announced[-1:] == ([] if mode == "compromised" else [mode])  # an attacker announces nothing
    assert ls.world.service_modes()["core-ledger"]["mode"] == mode


def test_publish_cve_marks_php_below_8_1(ls):
    cmd(ls, "publish-cve")
    status = {sid: s["cve"][0]["status"] for sid, s in ls.world.service_modes().items()}
    assert status == {"market-feed": "vulnerable_non_patchable", "news-wire": "non_concerne", "kyc-docs": "vulnerable_patchable",
                      "payment-gateway": "vulnerable_non_patchable", "core-ledger": "vulnerable_non_patchable"}
    alert = [a for a in ls.world.observer.alerts if a["kind"] == "cve_published"][0]
    assert alert["from"] == "secu" and set(alert["unpatchable"]) == {"market-feed", "payment-gateway", "core-ledger"}


def test_fake_agent_is_rejected(ls):
    cmd(ls, "fake-agent --as risk")
    cmd(ls, "fake-agent --as trader-9")
    invalid = list(ls.world.observer.invalid)
    assert [i["claimed_from"] for i in invalid] == ["risk", "trader-9"]
    assert not any(i.get("notional") == 20_000_000 for i in ls.world.ledger.exec_info.values())


def test_kill_agent_and_vigie(ls):
    cmd(ls, "kill trader-2")
    cmd(ls, "kill-vigie")
    ls.run(8)
    view = ls.world.agents_view()
    assert not view["trader-2"]["alive"] and view["trader-1"]["alive"]
    assert ls.agents["risk"].light == "ORANGE"


def test_set_commands(ls):
    cmd(ls, "set herding 0.8")
    cmd(ls, "set guardrails on")
    cmd(ls, "set restricted CITRA,ACME")
    assert ls.params.all() == {"herding": 0.8, "guardrails": "on", "restricted_list": ["CITRA", "ACME"]}
    with pytest.raises(ValueError):
        cmd(ls, "set herding 1.5")
    with pytest.raises(ValueError):
        cmd(ls, "set guardrails peut-etre")
    with pytest.raises(ValueError):
        cmd(ls, "exploit market-feed")


def test_light_human_news_invoice_commands(ls):
    cmd(ls, "light ROUGE")
    assert ls.agents["quant"].paused
    cmd(ls, "human resume")
    assert not ls.agents["quant"].paused
    before = ls.world.market.prices["BOREAL"]
    cmd(ls, 'news BOREAL -6 "BOREAL : avertissement"')
    assert ls.world.market.prices["BOREAL"] == pytest.approx(before * 0.94)
    cmd(ls, "invoice BEN-01 300000")
    assert ls.agents["payment"].sent == 1


def test_process_target_kills_by_pid(cfg):
    store = fakeredis.FakeRedis(decode_responses=True)
    target = ProcessTarget(cfg, client=store)
    proc = subprocess.Popen(["sleep", "30"])
    store.set("agent:trader-2:pid", proc.pid)
    assert "débranché" in target.kill("trader-2")
    assert proc.wait(timeout=5) != 0
    assert "inconnu" in target.kill("trader-7")


# --- scénarios ------------------------------------------------------------------------------
def run(cfg, name, **kwargs):
    world = Lockstep(cfg, **kwargs)
    ticks = schedule_scenario(world, load_scenario(SCENARIOS / f"{name}.yaml"))
    return world, world.run(ticks)


def test_all_scenarios_load():
    names = {p.stem for p in SCENARIOS.glob("*.yaml")}
    assert names == {"php2shell_day", "rumeur_mimetisme", "ledger_legacy", "dossier_piege", "faux_agent", "vigie_debranchee"}


def test_scenario_php2shell_day(cfg):
    world, state = run(cfg, "php2shell_day")
    assert [(t, " ".join(a)) for t, a, _ in world.scenario_log if t > 0] == [
        (30, "publish-cve"), (40, "compromise market-feed"), (40, "compromise payment-gateway"), (90, "compromise news-wire")]
    losses = state["losses"]["losses_by_service_eur"]
    assert losses["payment-gateway"] > 1_000_000 and "market-feed" in losses
    assert {"quant", "trader-1", "risk", "payment"} <= {c["agent"] for c in state["contaminated_agents"]}
    assert any(t["iban_substituted"] for t in state["trapped_operations"])
    # Risk ne voit pas le risque : sa VaR (prix du Quant piégé) est loin de la vraie
    assert state["var_99_1d_eur"] > state["risk_view_vs_truth"]["risk_sees"]["var_eur"] * 2


def test_scenario_ledger_legacy_hides_losses_from_risk(cfg):
    world, state = run(cfg, "ledger_legacy")
    assert state["cve_published"] == []
    truth, seen = state["risk_view_vs_truth"]["truth"], state["risk_view_vs_truth"]["risk_sees"]
    assert truth["pnl_day_eur"] < -2_000_000 < seen["pnl_day_eur"]
    assert not world.agents["risk"].halted


def test_scenario_ledger_legacy_control(cfg):
    """Mêmes chocs, core-ledger sain : Risk arrête le trading."""
    world = Lockstep(cfg)
    scenario = load_scenario(SCENARIOS / "ledger_legacy.yaml")
    scenario["steps"] = [s for s in scenario["steps"] if "compromise" not in s.get("do", "")]
    world.run(schedule_scenario(world, scenario))
    assert world.agents["risk"].halted


def test_scenario_dossier_piege(cfg):
    world, state = run(cfg, "dossier_piege")
    ben07 = [i for i in world.world.observer.trapped if i["beneficiary_id"] == "BEN-07"]
    assert len(ben07) == 1 and ben07[0]["data_altered"]                    # piégé avec garde-fous off
    assert "BEN-07" in world.agents["compliance"].blocked                  # bloqué avec garde-fous on


def test_scenario_faux_agent(cfg):
    world, state = run(cfg, "faux_agent")
    assert len(state["invalid_messages"]) == 2
    assert all(a["kind"] == "invalid_signature" for a in state["alerts"] if a["severity"] == "critical")
    assert state["trapped_operations"] == []


def test_scenario_vigie_debranchee(cfg):
    world, state = run(cfg, "vigie_debranchee")
    assert world.world.executor.light == "ORANGE"
    assert state["decisions"].get("execution:refused", 0) > 0
    assert state["trapped_operations"] == [] and state["pnl"]["payment_fraud_eur"] == 0
