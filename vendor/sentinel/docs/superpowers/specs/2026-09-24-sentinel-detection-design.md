# Sentinel: Stage 2 Design: Features and Detectors

Date: 2026-09-24. Builds on `2026-09-24-sentinel-simulator-design.md` (Stage 1, merged, tag `difficulty-frozen`).

## Decisions made

| Decision | Choice | Reason |
|---|---|---|
| Feature computation | One streaming `WindowFeatureExtractor`, used for training, live engine and as the Java parity reference | Removes train/serve skew; one definition of every feature |
| Data protocol | Train/validate on seeds 7, 8, 9 (time split 70/30, episode-grouped, boundary purge); final test is a fresh simulation, seed 100, generated once | Test data did not exist when any modeling decision was made; larger per-cell counts |
| Rules baseline | Textbook thresholds (pre-registered by commit) and tuned thresholds (random search on validation) | Model must beat a fair baseline, and naive rules' failure is demonstrated |
| Model framing | One LightGBM multiclass model (Normal / Spoofing / Layering / Wash); Isolation Forest binary for comparison | One model to port; explanations name the predicted type |

## 1. Feature extractor

`WindowFeatureExtractor` consumes event rows one at a time in non-decreasing timestamp order (out of order raises). Per (trader, instrument, window) it keeps live-order state (submit time, qty, side, price, size relative to same-side best-level quantity at submission) and running fill counts. When the first event of a later window arrives for an instrument, it closes the previous window and emits one feature row per active trader. A window's features exist only when it closes, so alert delay is up to one window length (10 s): a detection-power/delay trade-off to state in the README.

**Schema addition (no behavior change):** EXEC rows gain `counterparty_id` (the other party's trader id; 0 for non-EXEC rows), as real trade reports carry both parties. Simulator randomness is untouched, so data is identical apart from the new column and the freeze tag stands.

## 2. Features (~25 per trader-window; no IDs, raw timestamps or instrument names)

| Group | Features |
|---|---|
| Activity | n_new, n_cancel, n_modify, n_fills, order_to_trade_ratio |
| Cancel behavior | cancel_rate, cancel_within_1s_share, cancel_within_3s_share, median_cancelled_lifetime_s |
| Size vs. book | max_order_qty, max_qty_over_best, peak_resting_over_depth5 |
| Layering | max_live_levels_same_side, max_cancel_burst_100ms |
| Spoof direction | opposite_fill_share, signed_mid_drift_ticks |
| Wash | net_over_gross, top_counterparty_share, n_counterparties, roundtrip_counterparty_share |

Market makers cancel ~99% legitimately, so single cancel features mislead; signal comes from combinations.

## 3. Rules baseline

Typed rules: SPOOFING (cancel_rate, max_qty_over_best, cancel_within_3s_share, opposite_fill_share), LAYERING (max_live_levels_same_side, max_cancel_burst_100ms, cancel_rate), WASH (top_counterparty_share, net_over_gross, n_fills). Generic reference rule: cancel_rate >= 0.9 or order_to_trade_ratio >= 50 (untyped). Textbook thresholds are committed before any validation result is viewed (pre-registration). Tuned thresholds: 2,000-draw random search on validation, maximizing F1 per type (ties to higher precision). Every rule alert has a trace of fired conditions and values.

## 4. Models, SHAP, alerts

- LightGBM multiclass: capped inverse-frequency class weights, 15-leaf trees, early stopping on validation log-loss, small enough to export to Java. Per-type thresholds chosen on validation to maximize F1; highest probability wins ties.
- Isolation Forest: same features, trained without labels; one score, one threshold; evaluated as "any manipulation" and per type vs. normal.
- SHAP: `TreeExplainer`; for an alert of type k, top 5 positive class-k contributors rendered with value and plain-language template. Generated for alerts only, by a Python service outside the per-event hot path.
- Alert: trader, instrument, window, type, score, source (rule|model), explanation list (feature, value, contribution, phrase); rule alerts use the same shape with conditions in place of SHAP contributions.

## 5. Data protocol, metrics contract, tests, risks

- Splits: seeds 7, 8, 9; within each run, first 70% of windows train, remaining 30% validation; episodes straddling the boundary and windows adjacent to it dropped. Test: seed 100 in `data/test/`; loader requires `--final` and appends to an access log.
- Metrics contract (for Stage 4): per type, positives are that type's windows, negatives are all NORMAL windows; other types' windows excluded from that type's precision/recall/FPR; also cut by difficulty.
- Tests: hand-computed value per feature; window close semantics and out-of-order rejection; feature-name denylist (id, ts, time, instrument); SHAP additivity (contributions + base = raw score); rule traces; threshold search; model training; determinism.
- Risks: validation overfitting through feature iteration (mitigated by the fresh test simulation); near-perfect easy-case scores (expected, report per difficulty); large trees complicating the Java port (tree size capped).

## Out of scope for Stage 2

Streaming engine loop, Java port, latency/throughput measurement, final test-set evaluation, PR-curve charts and RESULTS.md (Stages 3-4), README (Stage 5).
