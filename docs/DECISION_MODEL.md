# Decision model — combining every signal into one trade decision and size

**Status:** design v0.1 (2026-10-17). Supersedes the "one trigger + pass/fail gates" design of trader_v0/v1 for research; the rule strategies remain as benchmarks.

## 1. Problem with the rule strategies

trader_v0/v1 let one event (the 9/24 EMA cross) decide, used the other indicators only as yes/no gates, and sized every trade identically. The 2026-10-17 learning pass showed the cross alone carries ≈ 0 edge before costs (39–45K signals). Information that might carry edge (volume vs normal, float, VWAP position, MACD slope, distance to highs, time of day, cost to trade) never got weighed against each other, and position size ignored how good the setup looked.

## 2. Pipeline

```
bar close ──▶ candidate triggers ──▶ feature vector x (≈32 point-in-time features)
                                         │
             hard vetoes (universe, regime, risk) ──▶ reject
                                         ▼
       models: μ(x) = E[net return],  p(x) = P(net > 0),  σ(x) = model disagreement
                                         ▼
       policy: trade iff μ − z·σ > h and p ≥ p_min
               size = ¼-Kelly(p, b(x)) × allocation, capped by risk limits
                                         ▼
       existing engine: order lifecycle, risk gate, brokers (unchanged)
```

## 3. Candidates (when to ask the question)

A candidate is evaluated on a bar close when any trigger fires and the symbol is flat: EMA 9/24 cross up, MACD line cross up, VWAP reclaim (close crosses above session VWAP), high-of-day break, pre-market-high break, PSAR flip up. Which trigger fired is itself a feature (one-hot). Asking at every bar is rejected: it multiplies correlated, near-duplicate samples without adding information.

## 4. Features (all computed from data strictly before the decision; scale-free)

| Family | Features |
|---|---|
| Trend | (EMA9−EMA24)/ATR; EMA9 slope over 3 bars / ATR; MACD hist / ATR; Δ MACD hist (1 bar); PSAR distance / ATR; SuperTrend side; ADX(14) |
| Momentum | RSI(14); ROC(5), ROC(15); EWO / price |
| Volume / flow | bar rel-vol (log); cumulative session rel-vol (log); PVT − PVT signal (z over 30 bars); PVT slope; (close − VWAP)/ATR; float rotation (log) |
| Volatility | ATR(14)/price; Bollinger width(20, 2) |
| Structure | (close − high of day)/ATR; (close − pre-market high)/ATR; bars since session high |
| Context | minute of day (sin/cos + window one-hot); log price; log free float (NaN if unknown); gap %; dilution flag; estimated round-trip cost %; segment (pre/rth/post) |
| Trigger | one-hot of the trigger that fired |

Missing values stay NaN: gradient-boosted trees handle them natively, and the linear model gets train-set medians plus a missing-indicator column. Every feature has a point-in-time test (perturbing future bars must not change it).

## 5. Labels (what "good" means)

The label is the realized net return of the exit policy the strategy will actually use: enter at the next bar open plus half-spread; exit at the first of a profit target at k_tp·ATR, a stop at k_sl·ATR, or a time limit of H bars, minus half-spread; same session only. ATR-scaled brackets replace fixed percentages because the fixed −2% stop fired in 54% of trader_v1 trades, on stocks whose normal 30-minute swing is 2–3%. (k_tp, k_sl, H) is a small grid selected inside the training folds only. Labels are winsorized at the 1st/99th percentile for regression so a few pump-and-dumps do not dominate.

## 6. Models (lightweight, explainable)

- **μ(x), expected net return:** ensemble mean of a ridge regression (standardized features + missing indicators) and a shallow LightGBM regressor (depth ≤ 4, ≤ 300 trees), each bagged over 5 bootstrap resamples of training *sessions* (not rows, so correlated same-day signals stay together).
- **σ(x), uncertainty:** standard deviation of the 10 bagged members' predictions.
- **p(x), win probability:** calibrated LightGBM classifier (existing `GBMMetaFilter`).
- **b(x), payoff ratio:** average win ÷ average |loss| in the training data, tabulated by predicted-probability decile and smoothed toward the global ratio.
- Inference budget: < 2 ms per candidate on CPU; artifacts < 20 MB; every decision journals its top feature contributions.

## 7. Policy (the decision matrix and the size)

- **Trade** iff μ − z·σ > h and p ≥ p_min, and no hard veto. Defaults z = 1, h = 0 (costs are already in the labels), p_min = 0.5; z, h and p_min are tuned on training folds only.
- **Kelly fraction:** f* = p − (1 − p)/b. Use f = clip(¼ · f*, 0, f_max), with f_max = 2% of allocation at risk per trade.
- **Shares:** qty = (f × allocation) / (entry − stop), with stop = entry − k_sl·ATR. Then apply the existing caps: max notional, buying power, participation, symbol exposure, daily loss / budget limits.
- **Hard vetoes (never overridden by the model):** universe (price band, float cap, fail-closed float), halted, data stale, session cutoff, risk-gate rejects.

## 8. Validation (unchanged bar, applied to the whole policy)

- Purged, session-grouped walk-forward with embargo ≥ H bars; every hyper-parameter is chosen inside the training fold.
- Out-of-sample metrics:
  - AUC and calibration of p
  - correlation of μ with realized return
  - **policy P&L** after costs, using the same sizing
  - expectancy CI, profit factor, max drawdown, deflated Sharpe over all configurations tried
- The final model is re-run through the full engine on the lockbox sessions (once). Promotion gates from plan §1/§9.2 apply: it must beat the rule strategies, take-every and random entry.
- If no configuration passes, the output is NO-TRADE. That is a valid result.

## 9. Integration

A new strategy type (`kind: model` YAML) supplies triggers, vetoes, the exit-bracket parameters and the model artifact path. A `DecisionRuntime` emits the same `Decision` objects as `StrategyRuntime`, so the engine, order lifecycle, risk gate, brokers, live runner and replay parity are unchanged. New ATR-scaled exit conditions are added (`atr_take_profit`, `atr_stop`). Training: `scripts/train_decision_model.py --start --end --lockbox-start`, which writes the artifact plus a report.

## 10. v0.2 changes from the adversarial design review (2026-10-17)

The review graded v0.1 "premature". Every change below is binding.

| Review item | Change |
|---|---|
| F1: costs modeled, not measured | `MeasuredCostModel` built from Alpaca SIP NBBO quotes sampled at decision/exit times (median relative spread per symbol × time-of-day bucket, with a bucket-model fallback flagged in the report). Every result is reported under a ±50% spread shock; configurations whose sign flips are rejected. The policy hurdle h is at least 0.5 × the measured round trip until live fills calibrate it. First probe: YMAT measured 0.37% full spread vs 1.0% assumed. |
| F2: survivorship / universe | A symbol-session enters the dataset only on or after the session the symbol was discovered (`tracked_symbols.first_seen_session`), never before. The Telegram alert universe gets added when credentials arrive. Coverage and discovery sources go in every report. |
| F3: point-in-time float | Float features carry a quality flag (vendor snapshot = good; EDGAR proxy older than its filing by > 90 days, or dilution flag set = degraded). Float-dependent features are excluded when the degraded share exceeds a threshold, and edge sensitivity to float is reported. |
| F4: effective sample size | Effective N = number of distinct (symbol, session) clusters with signals. Model complexity is tied to N: ≤ 5 features (ridge only) below 300 clusters, ≤ 12 below 1,000, the full set above. CIs use a cluster bootstrap over (session, symbol). |
| F5: trial accounting | A persistent trial ledger counts every configuration ever evaluated on the same data (including the 2026-10-17 passes). The true cross-trial Sharpe variance feeds the deflated Sharpe ratio. |
| F6: regime instability | Promotion requires out-of-sample expectancy with the same sign in **every** fold, not just pooled. |
| F7: Kelly overbetting | p is shrunk toward 0.5 and b toward the global ratio by estimation-error weight; Kelly uses the lower CI of p; a portfolio at-risk cap applies across correlated open positions; sizing is fixed-fractional until N ≥ 1,000 clusters. |
| F8: label overlap / leakage | Session-grouped CPCV with embargo ≥ H bars; overlapping labels get uniqueness weights. A label-shuffle null must score AUC ≈ 0.5 and μ ≈ 0, or the run is invalid. Every feature has a point-in-time test. |
| F9: anti-goal risk | NO-TRADE remains a first-class output. The smallest model that beats the benchmarks wins. |

### Dataset contract (shared by the builder and trainer workstreams)

Parquet at `data/decision/<dataset_id>/dataset.parquet`, one row per candidate:

- **Identity:** `symbol`, `ts` (UTC, signal bar open), `session_date`, `trigger` (str)
- **Features:** `f_*` columns (float64, NaN allowed); quality flags `q_float_degraded`
- **Costs:** `cost_rt_measured`, `cost_rt_model`, `cost_source`
- **Labels:** for each (k_tp, k_sl, H) in the grid, `y_net__tp{k_tp}_sl{k_sl}_h{H}` (net return), `y_exit_bar__…` (bars held), `uniq_w__…` (uniqueness weight)
- **Discovery:** `discovered_session`

A JSON sidecar records feature definitions, the label grid, the cost-model version, the data range and the source store.
