# ARGUS

ARGUS combines a synthetic market, surveillance console, and investigation tools.

## Repository layout

- `src/argus/`: API, market orchestration, detection integration, and investigations.
- `frontend/`: React surveillance console.
- `scripts/prepare_argus.py`: ARGUS model preparation.
- `vendor/sentinel/`: Sentinel simulator, detectors, Java engine, tests, documentation, and results.
- `vendor/SD-FMM/`: upstream SD-FMM reference submodule.
- `argus-presentation.html`: merged scrolling presentation.
- `sites/trader-exchange/dist/index.html`: standalone exchange visualization.

Initialize submodules after cloning:

```bash
git submodule update --init --recursive
```

## ARGUS presentation

Open [argus-presentation.html](argus-presentation.html) in a browser for the combined presentation. Scroll down through the 3D market, order book, AI model diagram, and reference-study performance metrics.

The merged presentation embeds all four screens in one HTML file.

## SD-FMM reference implementation

The upstream [SD-FMM project](https://github.com/ys-Dai/SD-FMM) is included as a Git submodule in [`vendor/SD-FMM`](vendor/SD-FMM), pinned to commit `0d1a2158ee849fdc31a127bd049e622174f5c893`. Its original source and MIT license are preserved.

After cloning this repository, fetch the submodule with:

```bash
git submodule update --init --recursive
```

See its [README](vendor/SD-FMM/README.md) for setup and training instructions. This is a reference implementation; it is not yet connected to the ARGUS application or presentation.

## ARGUS local surveillance MVP

ARGUS wraps the Sentinel exchange in a live, controllable ten-agent demo and adds a React surveillance console, a synchronized-behavior graph, evidence-backed investigations, human-reviewed restrictions, and an SQLite audit trail.

Install the API extras and frontend packages:

```bash
python -m pip install -r requirements-dev.txt
cd frontend && npm install
```

Run the API and UI in separate terminals:

```bash
uvicorn argus.api:app --app-dir src --host 127.0.0.1 --port 8000
```

```bash
cd frontend && npm run dev
```

Open the local Vite address shown in the frontend terminal, then select **Start demo**. The seeded run includes ten agents; three repeatedly place and cancel orders in close succession. ARGUS detects repeated cross-agent patterns from observed events, opens a case, and proposes a temporary restriction. The exchange applies it only after an analyst approves. Reset starts a clean run; use the same seed to reproduce the behavior.

Sentinel's rules run immediately. For ARGUS-specific training, run `python scripts/prepare_argus.py` from this repository root. For the standalone Sentinel experiments, use its [reproduction guide](vendor/sentinel/README.md#reproducing-everything) from `vendor/sentinel`; its relative model and data paths are local to that directory. The training step also saves normal-feature reference values used by the investigator. Without `ARGUS_LLM_API_KEY`, ARGUS displays a deterministic evidence-based investigation brief. To enable an OpenAI-compatible investigator, set `ARGUS_LLM_API_KEY`, and optionally `ARGUS_LLM_BASE_URL` and `ARGUS_LLM_MODEL`; investigations contain synthetic demo evidence only.

The demo uses SQLite at `data/argus.sqlite3` by default. Set `ARGUS_DB_PATH` to use another local database file.

## Sentinel development

Install both local packages with `python -m pip install -r requirements-dev.txt`. Run Sentinel commands from `vendor/sentinel` so relative data and model paths resolve correctly. Existing root-level data is retained; pass an explicit path when reusing it.

```bash
cd vendor/sentinel
python -m pytest
```

See [Sentinel documentation](vendor/sentinel/README.md) and [results](vendor/sentinel/RESULTS.md).
