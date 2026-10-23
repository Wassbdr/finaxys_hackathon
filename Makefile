# Banque Horizon : environnement de simulation (hackathon IA et Finance)
PY ?= .venv/bin/python
NO_LLM ?=
NO_VIGIE ?=
SCENARIO ?= scenarios/php2shell_day.yaml
RUN ?= runs/main
T0 ?= 0
T1 ?= 300
FLAGS = $(if $(NO_LLM),--no-llm) $(if $(NO_VIGIE),--no-vigie)

.PHONY: install keys run run-honcho run-no-llm run-no-vigie test lockstep scenario inject report verify replay compare procfile

install:
	python3 -m venv --without-pip .venv
	pip3 --python .venv/bin/python install -r requirements.txt

keys:
	$(PY) -m sim.keys

procfile:
	$(PY) -m sim.launch procfile $(FLAGS)

# Superviseur maison : débrancher un agent ne coupe pas les autres (honcho, lui, arrête tout)
run:
	$(PY) -m sim.launch run $(FLAGS)

run-honcho: procfile
	$(PY) -m honcho -f Procfile.generated start

run-no-llm:
	$(MAKE) run NO_LLM=1

run-no-vigie:
	$(MAKE) run NO_VIGIE=1

test:
	$(PY) -m vigie.testing

lockstep:
	$(PY) -m sim.lockstep $(if $(NO_VIGIE),--no-vigie)

scenario:
	$(PY) -m sim.inject scenario $(SCENARIO)

report:
	$(PY) -m journal.report incident $(T0) $(T1) --run $(RUN)

verify:
	$(PY) -m journal.writer verify $(RUN)/journal.jsonl

replay:
	$(PY) -m api.replay $(RUN)

compare:
	$(PY) -m sim.compare

.PHONY: couche3 eval-couche3
couche3:
	$(PY) -m vigie.main launch $(if $(NO_LLM),--no-llm)

eval-couche3:
	HORIZON_NO_LLM=1 $(PY) -m vigie.eval

# Les trois banques partagent une seule horloge ; le scénario reste surchargeable.
DEMO_SCENARIO = $(if $(filter command line environment,$(origin SCENARIO)),$(SCENARIO),vigie/scenarios/jour_noir.yaml)
PORT ?= 8010
SPEED ?= 1.2
SEED ?= 42
.PHONY: classique compare-classique demo-live demo-replay demo-measure
classique:
	$(PY) -m vigie.classique --launch
compare-classique:
	$(PY) -m vigie.classique_eval
demo-live:
	HORIZON_NO_LLM=1 $(PY) -m vigie.demo_live live --scenario $(DEMO_SCENARIO) --seed $(SEED) --port $(PORT) --accelerator $(SPEED)
demo-replay:
	HORIZON_NO_LLM=1 $(PY) -m vigie.demo_live replay --port $(PORT) --accelerator $(SPEED) $(if $(RECORDING),--recording $(RECORDING))
demo-measure:
	HORIZON_NO_LLM=1 $(PY) -m vigie.demo_live measure --scenario $(DEMO_SCENARIO)

.PHONY: propositions
propositions:
	$(PY) -m vigie.propositions --run "$(RUN)"
