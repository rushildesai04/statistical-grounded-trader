# Pre-registration: the daily per-stock model on AAOI (2026-11-06)

Committed BEFORE the model is run on AAOI.

## Why
The owner asked how our trained per-stock model does on AAOI (the trader's favourite name). AAOI was only tested with
the opening-range breakout (FAIL); the daily model was never run on it. Same frozen code and parameters as
prereg_megacap_oos_2026-10-30 (`megacap_oos_study`: daily features + 15:49 intraday features, LightGBM, monthly
walk-forward from 2018, 1-session embargo, long/short/flat by in-sample quantile thresholds).

## Cost and gate (same checks as megacap_oos; AAOI is a mid cap, so the cost is higher)
Primary cost 0.10% per round trip (secondary: the megacaps' 0.04%). PASS needs ALL: day bootstrap lower 95% of mean
daily net > 0; 2x-cost mean > 0; >= 6 calendar years positive. Reported: Sharpe vs buy-and-hold, hit rate, by year,
last 3 months. AAOI is added to the Model Tape either way. PASS -> AAOI joins the forward test (paper) next month.
Trial ledger run id `aaoi-daily-2026-11-06`.

## Result (2026-11-06, run once, trial ledger `aaoi-daily-2026-11-06`) — FAIL
AAOI 2018-2026 out of sample, 0.10% cost: mean daily net -0.039% (lower 95% -0.165%), Sharpe -0.19; model -6.7%/yr vs
buy-and-hold +54.0%/yr (hold Sharpe 0.52); hit 50%; in the market 20% of days. Years: 2018 -34.6%, 2019 -1.8%,
2020 -25.4%, 2021 +22.9%, 2022 +15.3%, 2023 -6.1%, 2024 -48.7%, 2025 -25.3%, 2026 +29.7%. Last 3 months +43.7% vs
hold -2.3% (one good stretch in an erratic record). Checks: all three FAIL. Not added to the forward test.
