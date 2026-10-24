# Sentinel: Stage 3 Design: Streaming Engine (Python reference and Java port)

Date: 2026-09-24. Builds on the Stage 1 and Stage 2 specs. Tags so far: `difficulty-frozen`, `rules-preregistered`, `simulator-v2`.

## Decisions made

| Decision | Choice | Reason |
|---|---|---|
| What lives in Java | The full detection loop: feature extractor port, rules, LightGBM tree evaluation | Real before/after speedup on the whole loop; Python is the test oracle |
| Latency definition | In-process per-event timing of `process(event)`, events pre-loaded; window-close events reported separately; Java warmup pass; timer overhead subtracted | Measures the engine, not the disk; shows where the tail comes from |
| Scaling | Single-threaded headline; instruments are independent so the design shards by instrument (stated, not benchmarked) | Keeps scope tight |
| Explanations | SHAP stays in Python, computed only for alerts after the fact | Keeps the per-event hot path free of SHAP |

## 1. Components

**Python** (`src/sentinel/stream/`):
- `engine.py`: `PythonEngine` wraps the existing `WindowFeatureExtractor`; on each window close it scores the closed rows with LightGBM and applies three rule sets (textbook, tuned, generic). `process(event)` returns rows, class probabilities and lightweight `StreamAlert`s (no SHAP).
- `export.py`: `events.bin` (fixed-width little-endian int64 records, 15 fields), `engine.properties` (window length, feature order, per-type thresholds, all rule conditions).
- `bench.py`: timing harness and percentile statistics.
- `java.py`, `parity.py`: build and run the Java engine, and compare it with the Python engine.

**Java** (`java/src/sentinel/`, plain `javac`, no dependencies): `EventReader`, `FeatureExtractor` (port), `Config` (properties and rules), `LgbmModel` (text-format parser and tree evaluator), `Engine`, `Main` (`run`, `bench`), `SelfTest` (model fixture check).

## 2. Interchange and parity

Events: 8-byte magic `SNTLEV01`, int64 count, then records of 15 int64 fields (ts, instrument index, event type code, order id, trader id, counterparty id, side, price, qty, best bid, best ask, bid qty1, ask qty1, bid depth5, ask depth5), memory-mapped by Java.

Parity contract, checked on real simulations:
- same set of feature rows (trader, instrument, window);
- count-valued features exactly equal; all other features within 1e-9 relative;
- class probabilities within 1e-9;
- identical alert sets (detector, type, trader, instrument, window) for each of model, textbook, tuned and generic.
Additionally a fixture of 200 feature vectors with Python-computed probabilities that the Java evaluator must reproduce (isolates the tree evaluator from the extractor).

## 3. Benchmark protocol

- Data: prefix of the seed-7 event stream (default 500,000 events). Seed 100 (test) is not used in Stage 3.
- Per engine: throughput pass (untimed per event) and latency pass (timed per event, including scoring when a window closes). Java runs two unmeasured warmup passes first. Timer overhead is measured with an empty loop and subtracted.
- Reported: throughput (events/s); median, p99, p99.9, max latency in ns; the same split into window-closing versus ordinary events; hardware, Python and JDK versions. Percentiles use the nearest-rank definition in both languages.
- Python is profiled with `cProfile` (top hotspots reported). Java: straightforward port first, then one profile-guided fix to the top hotspot; both versions reported, parity re-checked after the fix.
- In Stage 4 the final accuracy numbers come from the Java engine's own alerts on the test simulation.

## 4. Risks

1. Floating-point parity (median of an even count, division and summation order): caught by the parity check.
2. LightGBM text format details (decision-type bits, default directions, missing-value handling; our features are never NaN): covered by the fixture test.
3. Window-close events dominate the tail: hence the split reporting.
4. JIT and GC noise: reported honestly, with repeat-run spread.

## Out of scope for Stage 3

Final test-set evaluation, PR curves, RESULTS.md (Stage 4); README (Stage 5); SHAP in Java; multi-threaded benchmarking; a network or file-tailing feed handler.
