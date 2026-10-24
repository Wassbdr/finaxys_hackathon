# Data design log

Chronological record of changes to the simulated data and why. Kept so the methodology can be defended honestly.

## v1: tag `difficulty-frozen` (Stage 1)
Simulator, difficulty table and agent parameters fixed before any detector existed. A pre-detector code review
found and fixed leakage artifacts (immediate-fill limit orders identifying wash trades, oversized spoofs, self-trades,
periodic episode spacing) and the tag was moved once, before any model was trained.

## v2: tag `simulator-v2` (during Stage 2, AFTER first validation results)
**Trigger:** the first SHAP explanation of a WASH alert listed "largest order was 1.0x the quantity at the best price"
as a top reason. Investigation: 34% of WASH windows contained an order sitting exactly alone at the best price versus
0.3% of NORMAL windows. Cause: wash legs always rest alone at a tick inside the spread, while normal agents never quoted
inside the spread. That is a simulator artifact, not a property of wash trading.
**Change:** noise traders now post a share of limit orders (`P_IMPROVE = 0.10`) inside the spread.
**Effect (measured):** artifact share 34% -> ~16% for wash windows versus ~3% normal (enrichment ~100x -> ~5x); median spread
2 -> 1 tick. It cannot be fully removed because once the spread is one tick there is no interior left to improve into.
Validation scores fell (LightGBM any-manipulation F1 0.578 -> 0.452; WASH F1 0.544 -> 0.318), which is the expected
sign for removing a leak.
**Disclosure:** this change was motivated by detector output, so it is a departure from "frozen before any detector".
It was made to remove an artifact, not to improve scores; the change reduced them. All four datasets (including the
never-analyzed final test simulation, seed 100) were regenerated with the new code. The textbook rule thresholds
(tag `rules-preregistered`) were not touched. Residual limitation: wash orders are still somewhat more likely than normal
orders to be alone at the best price.
