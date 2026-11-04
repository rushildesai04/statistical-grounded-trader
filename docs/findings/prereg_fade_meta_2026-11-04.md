# Pre-registration: a learned filter for the alert fade ("brain" v1, meta-labeling) (2026-11-04)

Committed BEFORE any feature in this file is computed. Plan: `docs/research/plan_2026-11-04.md` W3.

## Question
The fade rule (prereg_alert_fade_2026-11-01) takes every qualifying alert. Can one model, pooled across all
tickers and using only what is known at the alert, learn WHICH alerts to skip so that total profit rises?
Pooled on ticker traits (not names), so it applies to tickers it has never seen. Meta-labeling is suited to
this case: the primary signal comes from outside market data (the channel).

## Events and label (no change to the trade)
Events: the fade study's training events (`reports/telegram/trades_pooled_first.csv`, split = train,
2025-01-03 .. 2026-06-30, n = 2,349), scored with `alert_fade_test.score` (short at entry, 20% stop, 60 min,
gapper cost + 0.5% locate). Label = that net return.

## Features (point in time: only 1-minute SIP bars that ENDED at or before the alert, same session)
`sgt.fast.meta.features`: log price; return since the 09:30 open; returns over the last 5, 15, 60 minutes;
distance below the session high so far; last-5-minute volume / mean 1-minute volume so far (a ratio, so feeds
with different volume levels compare); minutes since 09:30; day of week; alert type (integer code); number of
earlier sessions in the last 10 with a fade event for the symbol; SPY return since the open.
No volume levels, no borrow data (not available historically; collected forward from 2026-11-04 for v2).

## Model and validation
LightGBM regressor on the net label (winsorized at the training 1st/99th percentile): 200 trees, learning rate
0.03, 7 leaves, min 40 rows per leaf, feature fraction 0.8, bagging 0.8 every 1, seed 0. Monthly expanding
walk-forward from 2025-07 (each month predicted by a model trained only on earlier months; 6 months minimum).
Policies (declared): skip an event when its prediction is below the q-quantile of that model's in-sample
predictions, q in {0.20, 0.35, 0.50}; q = 0 is the champion (take all).

## Development gate (walk-forward 2025-07 .. 2026-06)
Metric: total net per session = sum of net over taken trades / number of sessions (profit, not win rate).
Selected q: the highest metric. The filter goes forward as a SHADOW challenger only if the selected q beats
q = 0 on that metric with a session-bootstrap 95% one-sided lower bound of the paired daily difference > 0
(10,000 resamples, seed 20261104). Otherwise FAIL (development); nothing is deployed.

## Forward decision (decisive; the 2026-07..10 held-out was already examined once, so it is secondary)
The frozen model (`config/models/fade_meta_v1.txt`, trained on all training events, sha256 in the trial
ledger) scores every forward event nightly from official SIP bars ending before the alert. At the fade forward
decision point (n >= 300 forward events, amendment F1): PROMOTE the filter (it decides which alerts are
offered for approval) only if its total net per session beats take-all with the session-bootstrap 95% lower
bound of the paired difference > 0. Otherwise it stays in shadow (or is retired if its point estimate is <= 0).

## Secondary (reported only)
Held-out 2026-07..10 result of the frozen model; feature importance; win rate and worst trade for each q.
Trial ledger run ids `fade-meta-2026-11-04-dev` and `fade-meta-v1-forward`.
