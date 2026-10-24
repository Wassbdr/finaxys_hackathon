# Simulator vs. a real LOBSTER sample

**Real data:** one day (2012-06-21, 09:30-16:00) of NASDAQ AAPL, level-5 LOBSTER sample files
(301,587 messages), obtained from a public Hugging Face mirror of LOBSTER's free academic sample
(`totalorganfailure/lobster-data`, folder `LOBSTER_SampleFile_AAPL_2012-06-21_5`). The mirror states no license, so the
files are kept in the gitignored `data/lobster/` folder and are not redistributed here; only these summary statistics are.
**Simulator:** the seed-7 event stream (1.6M events, four instruments). Ratios are used because prices, sizes and clocks
are in different units. Spread is in ticks (one cent for AAPL; one price step in the simulator).

**Format check:** the message file has six columns (time, type, order id, size, price, direction) and **no trader or
account identifier**, so LOBSTER samples can validate market statistics but cannot supply labeled manipulation data.

| statistic | simulator | LOBSTER sample |
|---|---|---|
| cancel_to_new | 0.944 | 0.854 |
| spread_p10_ticks | 1.000 | 7.000 |
| spread_p50_ticks | 1.000 | 15.000 |
| spread_p90_ticks | 2.000 | 25.000 |
| depth1_over_median_size | 4.298 | 1.020 |
| depth5_over_median_size | 40.846 | 6.445 |
| size_p50 | 208.000 | 100.000 |
| size_p90_over_p50 | 2.111 | 1.000 |
| size_p99_over_p50 | 3.716 | 3.000 |
| iet_ms_p90_over_p50 | 3.327 | 289.341 |
| iet_ms_p99_over_p50 | 6.664 | 1653.922 |
| iet_p50_ms | 5.718 | 0.869 |

## Reading

- **Close:** cancel-to-new ratio (0.94 simulated, 0.85 real) and the tail heaviness of order sizes (p99/p50 3.7 vs 3.0).
- **Far apart, and it matters for the detectors:**
  - *Spreads:* one tick in the simulator, about 15 ticks for AAPL that day.
  - *Book depth relative to order size:* the simulated book is roughly 4 to 6 times deeper.
  - *Order sizes:* real orders cluster on round lots (p90/p50 = 1.0); simulated sizes are smoothly lognormal.
  - *Timing:* real inter-event gaps are extremely bursty (p99/p50 of about 1,650); simulated arrivals are close to Poisson (p99/p50 of about 7).
- Features that use size relative to the book, cancel timing or burst counts therefore see distributions the real market
  would not produce. Detector performance measured here should not be assumed to transfer.
- One stock on one day is a single reference point, and the simulator was deliberately not tuned to close these gaps.
