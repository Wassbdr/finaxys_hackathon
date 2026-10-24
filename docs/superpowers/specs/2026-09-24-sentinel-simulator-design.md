# Sentinel — Stage 1 Design: Market Simulator and Labels

Date: 2026-09-24

## Project context

Sentinel is a trade surveillance engine that detects spoofing, layering and
wash trading. The full project has five stages, each with its own
spec → plan → implementation cycle:

1. **Simulator and labels** (this spec)
2. Features and detectors: rules baseline, LightGBM/XGBoost, Isolation Forest, SHAP explanations
3. Streaming engine: Python reference, then a Java port with a parity test
4. Evaluation: per-type metrics, PR curve, RESULTS.md
5. Polish: README, tests, repo structure

## Decisions already made

| Decision | Choice | Reason |
|---|---|---|
| Unit of alert and label | Trader-window (trader + instrument + time window, e.g. 5 min) | Manipulation is a behavior pattern over time; matches real surveillance |
| Simulator fidelity | Real price-time-priority matching engine with agent-based flow | Price impact of spoofing must be causal, not scripted |
| Manipulation difficulty | easy / medium / hard dial, frozen before any detector is built | Shows where ML beats rules; prevents tuning data to favor the model |
| Streaming engine | Python reference first, Java port last, parity-tested | Complete project even if the Java port slips; before/after throughput number |
| LOBSTER samples | Validation of simulator statistics only | Samples have no trader IDs (to be confirmed on download), so they cannot carry trader-window labels |

## 1. Repo structure and data contract

```
senital/
  src/sentinel/
    sim/         book.py (matching engine), agents.py, scenarios.py, runner.py
    features/    (stage 2)
    detect/      (stage 2)
    stream/      (stage 3)
    eval/        (stage 4)
  java/          (stage 3)
  tests/
  data/          generated events + labels (gitignored)
  RESULTS.md, README.md
```

**Event log**: one row per event.

Columns: `ts` (int ns), `instrument`, `event_type` (NEW / CANCEL / MODIFY / EXEC),
`order_id`, `trader_id`, `side`, `price` (int ticks), `qty`, plus the book
snapshot at the time of the event (best bid/ask, top-5 depth per side).

- Prices are integer ticks (no floating-point error); timestamps are integer ns (exact ordering).
- The snapshot is stored per event so depth-relative features can be computed in a streaming engine without lookahead.

**Label file**: separate from the event log. One row per
`(trader_id, instrument, window)` with `label ∈ {NORMAL, SPOOFING, LAYERING, WASH}`
and `difficulty ∈ {easy, medium, hard, none}`. Keeping labels out of the event
log prevents detectors from reading them by accident.

## 2. Matching engine and background flow

**Book**: price-time priority. A dict maps price to a deque of orders at that
level, plus an order-id index for O(1) cancel. Supports limit orders, market
orders, cancel and modify. A modify that increases size loses queue priority,
as on real exchanges.

**Background agents** (Poisson arrivals, different rates per type):
- Market makers: quote both sides near the mid and refresh often. Their cancel rates are high and legitimate, which is the main source of false positives for naive rules.
- Noise traders: random limit and market orders, cancelling a fraction.
- Momentum/informed traders: trade in bursts.

Order sizes are heavy-tailed (lognormal). About 4 instruments and ~50 traders
per instrument, targeting 1.2M+ events total. All randomness is seeded.

## 3. Scenario injection and difficulty

Manipulators are extra agents acting through the same matching engine.

- **Spoofing**: place a large order on one side, the price drifts on the book imbalance, trade on the opposite side at the better price, cancel the large order.
- **Layering**: like spoofing but with 3–6 orders stacked at successive price levels, cancelled together.
- **Wash trading**: two accounts under one controlling entity trade against each other at roughly the same price and size, creating volume with no change in net position. Both accounts are labeled.

Each scenario takes a difficulty parameter controlling: size relative to book
depth, time-to-cancel, dilution with legitimate orders, and for wash trading
the jitter in size/timing. Parameters are **frozen before any detector work**.
Injected traders draw sizes and timings from the same distributions as normal
traders to avoid trivial separating artifacts. Every window in which a
manipulator is active gets its label and difficulty recorded.

## 4. Validation, testing, risks

**LOBSTER validation** (script run at the end of stage 1). Compare simulator
vs. one real ticker on: spread distribution, cancel-to-submit ratio, order-size
distribution, inter-event times, depth by level. Mismatches are reported
honestly in the README limitations; the simulator is not tuned to match
perfectly. Before download, confirm the sample license and whether the
message file carries trader IDs. Samples stay gitignored; only comparison
statistics are published.

**Tests (pytest, written first)**:
- Matching engine: price-time priority, partial fills, cancel of a filled order rejected, modify losing priority, no crossed book after any event, filled quantity conserved on both sides.
- Property test: book invariants hold after random event sequences.
- Determinism: same seed gives byte-identical event logs.
- Scenarios: each injected scenario shows its signature (spoof order cancelled, layering has N levels, wash trades net to zero position); labels cover exactly the injected windows.
- Scale: 1M+ events generated within a time budget.

**Risks**:
1. Python speed at 1M events. Book operations are O(1); profile before optimizing.
2. Simulator artifacts leaking into detection. Mitigation: shared distributions between normal and injected traders; stage 2 checks that no single trivial feature separates the classes.
3. Class imbalance. Manipulation windows are roughly 1–2% of windows; evaluate with precision-recall, not accuracy.

## Out of scope for stage 1

Features, detectors, SHAP, the streaming engine, the Java port, evaluation
charts, RESULTS.md and the README. Those belong to later stages.
