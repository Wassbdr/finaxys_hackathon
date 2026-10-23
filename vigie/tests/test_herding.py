import copy

from sim.config import ROOT
from sim.inject import load_scenario
from sim.lockstep import Lockstep
from vigie.herding import THRESHOLDS, Herding
from vigie.scenario import install


def replay(cfg, herding):
    scenario = copy.deepcopy(load_scenario(ROOT / "scenarios" / "rumeur_mimetisme.yaml"))
    scenario["params"]["herding"] = herding
    ls = Lockstep(cfg, no_vigie=True, no_llm=True)
    h = Herding(cfg)
    ls.bus.tap(h.observe)
    try:
        ls.run(install(ls, scenario))
    finally:
        ls.close()
    return h.snapshot()


def test_rumour_separates_herding_0_from_0_8(cfg):
    calm, herd = replay(cfg, 0.0), replay(cfg, 0.8)
    for name, s in (("herding 0", calm), ("herding 0.8", herd)):
        print(f"{name}: mimetism index {s['worst']['mimetism_index']}, effective agents {s['worst']['effective_agents']}, "
              f"reflexive loop {s['reflexive_loop']['links']} links, depth {s['reflexive_loop']['max_depth']}")
    assert calm["worst"]["mimetism_index"] < THRESHOLDS["mimetism_index"] <= herd["worst"]["mimetism_index"]
    assert herd["worst"]["mimetism_index"] >= 3 * calm["worst"]["mimetism_index"]
    traders = herd["traders"]
    assert herd["worst"]["effective_agents"] <= THRESHOLDS["effective_share"] * traders < calm["worst"]["effective_agents"]
    assert calm["worst"]["effective_agents"] - herd["worst"]["effective_agents"] >= 1
    assert herd["reflexive_loop"]["max_depth"] >= THRESHOLDS["loop_depth"] > calm["reflexive_loop"]["max_depth"]
    assert {f["kind"] for f in herd["findings"]} == {"mimetism", "effective_agents", "reflexive_loop"}
    assert calm["findings"] == []


def test_status_carries_the_herding_figures(setup):
    c, _, _ = setup
    s = c.status()["herding"]
    assert s["traders"] == 3 and s["effective_agents"] == 3 and s["mimetism_index"] == 0
