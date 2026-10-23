# Banque Horizon : environnement de simulation (hackathon IA et Finance)
PY ?= .venv/bin/python
NO_LLM ?= 1
NO_VIGIE ?=
SCENARIO ?= scenarios/php2shell_day.yaml
RUN ?= runs/main
T0 ?= 0
T1 ?= 300
FLAGS = $(if $(NO_LLM),--no-llm) $(if $(NO_VIGIE),--no-vigie)

.PHONY: install keys run run-honcho run-no-llm run-no-vigie test lockstep scenario inject report verify replay compare metrics procfile

install:
	python3 -m venv --without-pip .venv
	pip3 --python .venv/bin/python install -r requirements.txt

keys:
	$(PY) -m sim.keys

procfile:
	$(PY) -m vigie.launch procfile $(FLAGS)

# Superviseur maison : débrancher un agent ne coupe pas les autres (honcho, lui, arrête tout)
run:
	$(PY) -m vigie.launch run $(FLAGS)

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
	$(PY) -m vigie.compare --scenario $(SCENARIO) $(if $(TICKS),--ticks $(TICKS))

# Banc A/B de tous les scénarios et de 10 journées propres : improvements.json (clé metrics) et vigie/METRIQUES.md
metrics:
	HORIZON_NO_LLM=1 $(PY) -m vigie.eval
