# Results

Evaluated on the untouched test simulation `test_seed100` (1,600,002 events, 24,664 trader-windows of 10 s, 1,067 manipulation windows from 377 injected episodes: Spoofing 293, Layering 295, Wash 479). Accuracy numbers come from the **Java streaming engine's own alerts**; the Isolation Forest is scored in Python on the same windows. Every metric is followed by a 95% cluster-bootstrap interval (2000 replicates; manipulation windows resampled by episode, normal windows by account). The data is synthetic: see the README's limitations before drawing conclusions about real markets.

![Precision-recall curves](docs/img/pr_curves.png)

## Detection performance

### Spoofing

| Detector | Precision | Recall | F1 | False-positive rate | Episode recall |
|---|---|---|---|---|---|
| generic rule | 0.01 [0.00, 0.01] | 0.13 [0.09, 0.17] | 0.01 [0.01, 0.01] | 0.2824 [0.2418, 0.3252] | 0.26 [0.18, 0.34] |
| textbook rules | 0.02 [0.01, 0.02] | 0.19 [0.15, 0.24] | 0.03 [0.02, 0.04] | 0.1368 [0.1220, 0.1531] | 0.41 [0.34, 0.50] |
| tuned rules | 0.02 [0.02, 0.02] | 0.25 [0.20, 0.30] | 0.04 [0.03, 0.04] | 0.1586 [0.1447, 0.1740] | 0.51 [0.42, 0.59] |
| LightGBM | 0.72 [0.64, 0.80] | 0.22 [0.17, 0.28] | 0.34 [0.28, 0.41] | 0.0011 [0.0007, 0.0015] | 0.51 [0.42, 0.60] |
| Isolation Forest | 0.02 [0.02, 0.03] | 0.39 [0.33, 0.44] | 0.04 [0.03, 0.05] | 0.2060 [0.1620, 0.2550] | 0.77 [0.70, 0.84] |

Average precision: LightGBM 0.314, Isolation Forest 0.020.

### Layering

| Detector | Precision | Recall | F1 | False-positive rate | Episode recall |
|---|---|---|---|---|---|
| generic rule | 0.01 [0.01, 0.01] | 0.20 [0.15, 0.24] | 0.02 [0.01, 0.02] | 0.2824 [0.2414, 0.3233] | 0.35 [0.27, 0.43] |
| textbook rules | 0.39 [0.35, 0.44] | 0.43 [0.39, 0.48] | 0.41 [0.38, 0.45] | 0.0083 [0.0070, 0.0097] | 0.93 [0.88, 0.97] |
| tuned rules | 0.84 [0.79, 0.89] | 0.40 [0.35, 0.45] | 0.54 [0.49, 0.59] | 0.0009 [0.0006, 0.0013] | 0.88 [0.82, 0.94] |
| LightGBM | 0.89 [0.83, 0.95] | 0.40 [0.34, 0.46] | 0.55 [0.49, 0.61] | 0.0006 [0.0003, 0.0010] | 0.85 [0.78, 0.90] |
| Isolation Forest | 0.02 [0.02, 0.03] | 0.41 [0.35, 0.47] | 0.05 [0.04, 0.06] | 0.2060 [0.1593, 0.2537] | 0.79 [0.72, 0.86] |

Average precision: LightGBM 0.534, Isolation Forest 0.022.

### Wash

| Detector | Precision | Recall | F1 | False-positive rate | Episode recall |
|---|---|---|---|---|---|
| generic rule | 0.01 [0.00, 0.01] | 0.08 [0.06, 0.10] | 0.01 [0.01, 0.01] | 0.2824 [0.2430, 0.3231] | 0.27 [0.18, 0.35] |
| textbook rules | 0.87 [0.64, 1.00] | 0.03 [0.01, 0.05] | 0.05 [0.02, 0.09] | 0.0001 [0.0000, 0.0002] | 0.08 [0.03, 0.13] |
| tuned rules | 0.44 [0.36, 0.52] | 0.15 [0.11, 0.19] | 0.22 [0.17, 0.28] | 0.0038 [0.0030, 0.0046] | 0.39 [0.29, 0.48] |
| LightGBM | 0.67 [0.59, 0.74] | 0.22 [0.17, 0.28] | 0.33 [0.27, 0.40] | 0.0022 [0.0016, 0.0029] | 0.50 [0.40, 0.59] |
| Isolation Forest | 0.02 [0.02, 0.03] | 0.22 [0.18, 0.28] | 0.04 [0.03, 0.05] | 0.2060 [0.1620, 0.2526] | 0.55 [0.46, 0.64] |

Average precision: LightGBM 0.309, Isolation Forest 0.023.

### Any manipulation type

| Detector | Precision | Recall | F1 | False-positive rate | Episode recall |
|---|---|---|---|---|---|
| generic rule | 0.02 [0.02, 0.02] | 0.13 [0.11, 0.15] | 0.03 [0.03, 0.04] | 0.2824 [0.2428, 0.3259] | 0.29 [0.25, 0.34] |
| textbook rules | 0.08 [0.07, 0.09] | 0.28 [0.25, 0.31] | 0.12 [0.11, 0.14] | 0.1443 [0.1297, 0.1599] | 0.66 [0.61, 0.71] |
| tuned rules | 0.08 [0.07, 0.10] | 0.33 [0.30, 0.36] | 0.13 [0.12, 0.15] | 0.1626 [0.1484, 0.1773] | 0.71 [0.67, 0.76] |
| LightGBM | 0.77 [0.73, 0.81] | 0.29 [0.26, 0.32] | 0.42 [0.39, 0.46] | 0.0039 [0.0031, 0.0048] | 0.67 [0.62, 0.71] |
| Isolation Forest | 0.07 [0.05, 0.08] | 0.32 [0.29, 0.35] | 0.11 [0.09, 0.13] | 0.2060 [0.1617, 0.2555] | 0.71 [0.66, 0.76] |

Average precision: LightGBM 0.502, Isolation Forest 0.061.

The generic rule is untyped, so its per-type rows repeat its overall flags. Episode recall is the share of injected cases with at least one window flagged.

## Recall by difficulty

![Recall by difficulty](docs/img/recall_by_difficulty.png)

### Spoofing

| Detector | Easy | Medium | Hard |
|---|---|---|---|
| generic rule | 0.09 [0.02, 0.17] (n=46) | 0.09 [0.03, 0.16] (n=86) | 0.16 [0.10, 0.21] (n=161) |
| textbook rules | 0.41 [0.27, 0.57] (n=46) | 0.22 [0.14, 0.32] (n=86) | 0.12 [0.07, 0.17] (n=161) |
| tuned rules | 0.52 [0.37, 0.67] (n=46) | 0.24 [0.16, 0.35] (n=86) | 0.17 [0.11, 0.23] (n=161) |
| LightGBM | 0.83 [0.72, 0.93] (n=46) | 0.24 [0.16, 0.34] (n=86) | 0.04 [0.01, 0.07] (n=161) |
| Isolation Forest | 0.83 [0.71, 0.93] (n=46) | 0.42 [0.32, 0.52] (n=86) | 0.24 [0.18, 0.31] (n=161) |

### Layering

| Detector | Easy | Medium | Hard |
|---|---|---|---|
| generic rule | 0.11 [0.02, 0.20] (n=45) | 0.18 [0.11, 0.27] (n=92) | 0.23 [0.16, 0.30] (n=158) |
| textbook rules | 0.91 [0.81, 1.00] (n=45) | 0.43 [0.38, 0.51] (n=92) | 0.30 [0.26, 0.35] (n=158) |
| tuned rules | 0.93 [0.83, 1.00] (n=45) | 0.36 [0.29, 0.43] (n=92) | 0.27 [0.23, 0.31] (n=158) |
| LightGBM | 0.96 [0.87, 1.00] (n=45) | 0.38 [0.30, 0.47] (n=92) | 0.25 [0.20, 0.30] (n=158) |
| Isolation Forest | 0.93 [0.83, 1.00] (n=45) | 0.40 [0.31, 0.50] (n=92) | 0.27 [0.20, 0.33] (n=158) |

### Wash

| Detector | Easy | Medium | Hard |
|---|---|---|---|
| generic rule | 0.01 [0.00, 0.04] (n=72) | 0.07 [0.03, 0.12] (n=161) | 0.11 [0.07, 0.14] (n=246) |
| textbook rules | 0.12 [0.04, 0.24] (n=72) | 0.01 [0.00, 0.03] (n=161) | 0.01 [0.00, 0.03] (n=246) |
| tuned rules | 0.51 [0.36, 0.66] (n=72) | 0.15 [0.09, 0.21] (n=161) | 0.04 [0.02, 0.07] (n=246) |
| LightGBM | 0.71 [0.57, 0.84] (n=72) | 0.23 [0.14, 0.32] (n=161) | 0.07 [0.04, 0.12] (n=246) |
| Isolation Forest | 0.65 [0.51, 0.79] (n=72) | 0.17 [0.09, 0.25] (n=161) | 0.13 [0.09, 0.18] (n=246) |

## Paired comparisons

Differences use the same bootstrap replicates for both detectors, so an interval that excludes 0 means the difference is distinguishable from resampling noise.

### Spoofing

| Comparison | Metric | Difference | Verdict |
|---|---|---|---|
| LightGBM minus tuned rules | f1 | +0.304 [+0.240, +0.371] | clear |
| LightGBM minus tuned rules | recall | -0.024 [-0.084, +0.038] | not distinguishable from 0 |
| LightGBM minus tuned rules | fpr | -0.158 [-0.173, -0.144] | clear |
| LightGBM minus textbook rules | f1 | +0.308 [+0.243, +0.374] | clear |
| LightGBM minus textbook rules | recall | +0.027 [-0.031, +0.086] | not distinguishable from 0 |
| LightGBM minus textbook rules | fpr | -0.136 [-0.152, -0.121] | clear |
| LightGBM minus Isolation Forest | f1 | +0.297 [+0.234, +0.362] | clear |
| LightGBM minus Isolation Forest | recall | -0.164 [-0.201, -0.124] | clear |
| LightGBM minus Isolation Forest | fpr | -0.205 [-0.254, -0.161] | clear |

### Layering

| Comparison | Metric | Difference | Verdict |
|---|---|---|---|
| LightGBM minus tuned rules | f1 | +0.010 [-0.026, +0.044] | not distinguishable from 0 |
| LightGBM minus tuned rules | recall | +0.000 [-0.032, +0.034] | not distinguishable from 0 |
| LightGBM minus tuned rules | fpr | -0.000 [-0.001, +0.000] | not distinguishable from 0 |
| LightGBM minus textbook rules | f1 | +0.136 [+0.090, +0.185] | clear |
| LightGBM minus textbook rules | recall | -0.037 [-0.075, +0.000] | not distinguishable from 0 |
| LightGBM minus textbook rules | fpr | -0.008 [-0.009, -0.007] | clear |
| LightGBM minus Isolation Forest | f1 | +0.503 [+0.446, +0.560] | clear |
| LightGBM minus Isolation Forest | recall | -0.014 [-0.062, +0.033] | not distinguishable from 0 |
| LightGBM minus Isolation Forest | fpr | -0.205 [-0.253, -0.159] | clear |

### Wash

| Comparison | Metric | Difference | Verdict |
|---|---|---|---|
| LightGBM minus tuned rules | f1 | +0.110 [+0.073, +0.150] | clear |
| LightGBM minus tuned rules | recall | +0.073 [+0.045, +0.105] | clear |
| LightGBM minus tuned rules | fpr | -0.002 [-0.003, -0.001] | clear |
| LightGBM minus textbook rules | f1 | +0.280 [+0.218, +0.342] | clear |
| LightGBM minus textbook rules | recall | +0.194 [+0.146, +0.245] | clear |
| LightGBM minus textbook rules | fpr | +0.002 [+0.002, +0.003] | clear |
| LightGBM minus Isolation Forest | f1 | +0.293 [+0.228, +0.356] | clear |
| LightGBM minus Isolation Forest | recall | -0.002 [-0.042, +0.041] | not distinguishable from 0 |
| LightGBM minus Isolation Forest | fpr | -0.204 [-0.250, -0.160] | clear |

### Any type

| Comparison | Metric | Difference | Verdict |
|---|---|---|---|
| LightGBM minus tuned rules | f1 | +0.289 [+0.251, +0.326] | clear |
| LightGBM minus tuned rules | recall | -0.039 [-0.070, -0.011] | clear |
| LightGBM minus tuned rules | fpr | -0.159 [-0.174, -0.144] | clear |
| LightGBM minus textbook rules | f1 | +0.299 [+0.258, +0.336] | clear |
| LightGBM minus textbook rules | recall | +0.013 [-0.021, +0.047] | not distinguishable from 0 |
| LightGBM minus textbook rules | fpr | -0.140 [-0.156, -0.126] | clear |
| LightGBM minus Isolation Forest | f1 | +0.314 [+0.273, +0.353] | clear |
| LightGBM minus Isolation Forest | recall | -0.028 [-0.054, -0.003] | clear |
| LightGBM minus Isolation Forest | fpr | -0.202 [-0.252, -0.158] | clear |

## Alert volume

Simulated span: 0.37 h across 4 instruments. "False alerts per 1,000 innocent windows" is the false-positive rate expressed as analyst workload.

### Spoofing

| Detector | True alerts | False alerts | False alerts per 1,000 innocent windows | False alerts per instrument-hour |
|---|---|---|---|---|
| generic rule | 37 | 6,663 | 282.4 | 4,531.6 |
| textbook rules | 57 | 3,227 | 136.8 | 2,194.7 |
| tuned rules | 72 | 3,742 | 158.6 | 2,545.0 |
| LightGBM | 65 | 25 | 1.1 | 17.0 |
| Isolation Forest | 113 | 4,862 | 206.0 | 3,306.7 |

### Layering

| Detector | True alerts | False alerts | False alerts per 1,000 innocent windows | False alerts per instrument-hour |
|---|---|---|---|---|
| generic rule | 58 | 6,663 | 282.4 | 4,531.6 |
| textbook rules | 128 | 197 | 8.3 | 134.0 |
| tuned rules | 117 | 22 | 0.9 | 15.0 |
| LightGBM | 117 | 14 | 0.6 | 9.5 |
| Isolation Forest | 121 | 4,862 | 206.0 | 3,306.7 |

### Wash

| Detector | True alerts | False alerts | False alerts per 1,000 innocent windows | False alerts per instrument-hour |
|---|---|---|---|---|
| generic rule | 39 | 6,663 | 282.4 | 4,531.6 |
| textbook rules | 13 | 2 | 0.1 | 1.4 |
| tuned rules | 71 | 90 | 3.8 | 61.2 |
| LightGBM | 106 | 53 | 2.2 | 36.0 |
| Isolation Forest | 107 | 4,862 | 206.0 | 3,306.7 |

### Any type

| Detector | True alerts | False alerts | False alerts per 1,000 innocent windows | False alerts per instrument-hour |
|---|---|---|---|---|
| generic rule | 134 | 6,663 | 282.4 | 4,531.6 |
| textbook rules | 297 | 3,405 | 144.3 | 2,315.8 |
| tuned rules | 353 | 3,838 | 162.6 | 2,610.3 |
| LightGBM | 311 | 92 | 3.9 | 62.6 |
| Isolation Forest | 341 | 4,862 | 206.0 | 3,306.7 |

## Streaming performance

Single-threaded, 500,000 pre-loaded events, per-event timing of `process(event)` including model and rule scoring when a window closes. Windows-11-10.0.26200-SP0, AMD64 Family 25 Model 117 Stepping 2, AuthenticAMD, 16 logical cores, Python 3.12.10, openjdk version "25.0.4.1" 2026-08-18 LTS. Timer overhead is subtracted where the timer can resolve it (Python: 100 ns; Java: measured as 0, because the Windows timer ticks in 100 ns steps), so Java's median is a quantised upper bound. These are processing times per event inside the engine, not the delay before an alert reaches an analyst: an alert for a window appears when that window closes (up to 10 seconds after the behaviour) plus the scoring time shown for window-closing events. Throughput and median agree: 2.2 million events per second is a mean of about 0.45 µs per event, above the 0.1 µs median, because the 164 window-closing events (about 1 ms each) account for roughly two thirds of the total processing time.

![Streaming performance](docs/img/performance.png)

| Engine | Events/s | Median | p99 | p99.9 | Max | Window-closing events: median | Other events: median |
|---|---|---|---|---|---|---|---|
| Python (reference) | 178,979 | 4.7 µs | 14.2 µs | 59.7 µs | 122.9 ms | 1.3 ms (n=164) | 4.7 µs |
| Java v1 (straight port) | 1,975,920 | 100 ns | 700 ns | 5.3 µs | 3.7 ms | 2.0 ms (n=164) | 100 ns |
| Java v2 (flattened trees) | 2,217,755 | 100 ns | 600 ns | 4.3 µs | 2.3 ms | 949.1 µs (n=164) | 100 ns |

Window-closing events are the few (about one in 3,000) that trigger scoring for every active trader; they define the far tail. Max latency is a single worst event (operating-system scheduling, garbage collection) and varies from run to run; the percentile columns are the stable ones.

## Frozen artifacts and run log

These files were hashed and tagged `stage4-frozen` before the test simulation was first loaded; the evaluation refuses to run if any hash differs.

| File | sha-256 |
|---|---|
| `java/src/sentinel/Config.java` | `381dcb390573e8df…` |
| `java/src/sentinel/Engine.java` | `3c24f9f2e6ed7af5…` |
| `java/src/sentinel/EventReader.java` | `2024764aa5b9e610…` |
| `java/src/sentinel/FeatureExtractor.java` | `1175f2aef743a902…` |
| `java/src/sentinel/LgbmModel.java` | `0d4b0d6b8d7fbde0…` |
| `java/src/sentinel/Main.java` | `fa4aa5015bd07f98…` |
| `java/src/sentinel/SelfTest.java` | `7ee5c3163a3d000a…` |
| `models/iforest.joblib` | `e9ad358a97229810…` |
| `models/iforest_threshold.json` | `3be23b279f95f734…` |
| `models/lgbm.txt` | `e0588e1724d2fcb0…` |
| `models/thresholds.json` | `a29460d7f4506008…` |
| `models/tuned_rules.json` | `9d5b95622b7b3f53…` |
| `src/sentinel/detect/rules.py` | `b03588845a4fb018…` |
| `src/sentinel/features/extractor.py` | `0eb5669684bc4e34…` |

- Evaluated at commit `d62cca4f8bbf0d0994ed5ea780c3dbf1557e4bfd` on 2026-09-24T22:47:29+00:00.
- Test-set access log: `2026-09-24T22:47:13+00:00	data\test_seed100`; `2026-09-24T22:47:13+00:00	data\test_seed100`

