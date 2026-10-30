# Pre-registration: per-ticker learning models, DAILY horizon (2026-10-30)

Committed BEFORE any daily-horizon feature is computed or model trained. Scored once.
Follow-up to `prereg_megacap_brains_2026-10-30.md` (30-minute horizon: FAIL; real but tiny signal,
+0.2..+4.4 bp gross per trade vs 4 bp cost). This is the second test on the same tickers and history;
both are in the trial ledger. If this fails, the search over historical data for these tickers stops.

## Hypothesis
A per-ticker model retrained monthly can predict the next close-to-close return well enough that,
trading once a day at the close, the edge per trade exceeds the cost. Daily moves are ~6x larger than
30-minute moves while the cost per trade is the same.

## Universe, data
Same 8 tickers (AAPL AMZN GOOGL MSFT META NVDA TSLA; RDDT from IPO), SPY/QQQ context. Alpaca
split/dividend-adjusted daily bars (`data/megacap/daily_*.parquet`, adjustment=all) and the raw 1-minute
store for today's session up to 15:49 ET (exchange-calendar closes; half days: 10 minutes before the
early close).

## Decision, label, execution
- Decision each trading day t at 15:50 ET (the market-on-close order cutoff). Features use daily bars
  through t-1 and today's 1-minute bars ending by 15:49 ET.
- Execution: market-on-close at t, exit market-on-close at t+1. Label = adjusted close(t+1) /
  adjusted close(t) - 1.

## Features (fixed, `src/sgt/megacap/daily.py`)
From adjusted daily closes through t-1: log returns over 1, 2, 5, 10, 21, 63, 126, 252 days; realized
volatility of daily returns over 21 and 63 days; distance of close(t-1) from its 20, 50, 200-day
moving averages; distance from the 252-day high and low; RSI(14) daily; 20-day dollar-volume ratio
(t-1 dollar volume / 20-day mean). Today (to 15:49): overnight gap (adjusted open / prior adjusted
close), log return open -> 15:49, log return over the last 60 minutes, intraday range (high-low)/open.
Market: SPY and QQQ today's return to 15:49 (gap + intraday) and 5- and 21-day returns; own today's
return minus SPY's. Calendar: day of week, month.

## Model and learning schedule
Per ticker: LightGBM regressor, fixed: 300 trees, learning rate 0.03, 7 leaves, min 100 rows per leaf,
feature fraction 0.8, bagging 0.8 every 1, seed 0; label winsorized at training 1st/99th percentiles.
Monthly expanding-window walk-forward from 2018-01 (needs >= 250 training sessions; RDDT starts later).
Long if prediction >= 90th percentile of its own training predictions, short if <= 10th, else flat.

## Costs and portfolio
Each position is a separate round trip: 4 bp (RDDT 10 bp). Portfolio daily return = mean over tickers
trading that day of each ticker's net return (flat = 0).

## Decision criteria (identical to the 30-minute test)
PASS only if ALL: (1) mean daily net > 0 with day-bootstrap one-sided 95% lower bound > 0 (10,000
resamples, seed 20261030); (2) annualized Sharpe >= 1.0; (3) mean still > 0 at 2x costs; (4) positive
in >= 6 of 9 calendar years 2018-2026; (5) mean > 0 from 2025-01 on. Per-ticker continuation (only if
the portfolio passes): one-sided lower bound > 0 at alpha 0.05/8. PASS -> passing tickers go to a
forward paper test (>= 3 months) before any real money.

## Secondary (never decides)
Long-only; 0.5x costs; pooled single model; per-ticker IC by year; gross bp per trade.
Trial ledger run id `megacap-daily-2026-10-30`.

## Run log
- 2026-10-30: first run crashed in the first model fit (the walk-forward's internal month column
  collided with the `month` feature). No prediction, return or output existed. Fixed (internal column
  renamed, regression test added) and run once.

## Result (scored once, 2026-10-30) — VERDICT: FAIL (near miss; first positive result in the project)
Walk-forward 2018-01-02 .. 2026-10-28 (2,218 days). Portfolio: mean daily net +0.028% (one-sided 95%
lower bound +0.004%), Sharpe 0.63, ~+7.0%/yr; 2x costs +0.019%/day; 2025+ +0.016%/day.
By year: 2018 +18.4%, 2019 -6.0%, 2020 +29.7%, 2021 +5.7%, 2022 +8.5%, 2023 -1.3%, 2024 -0.6%,
2025 +16.4%, 2026 -9.3%. Checks: lower bound > 0 PASS, Sharpe >= 1 FAIL (0.63), 2x costs PASS,
>= 6 positive years FAIL (5), 2025+ PASS. Per the frozen rule: FAIL.

| ticker | trades | gross bp/trade | hit | daily net | Sharpe | IC |
|---|---|---|---|---|---|---|
| AAPL | 421 | +14.3 | 52% | +0.020% | +0.29 | +0.009 |
| AMZN | 472 | +23.8 | 54% | +0.042% | +0.54 | +0.031 |
| GOOGL | 514 | +24.9 | 54% | +0.048% | +0.74 | +0.096 |
| MSFT | 559 | +17.7 | 55% | +0.035% | +0.48 | +0.069 |
| META | 536 | +15.7 | 52% | +0.028% | +0.30 | +0.017 |
| NVDA | 433 | +24.6 | 51% | +0.040% | +0.37 | +0.027 |
| TSLA | 365 | -3.4 | 50% | -0.012% | -0.11 | +0.004 |
| RDDT | 70 | -8.4 | 44% | -0.032% | -0.23 | -0.004 |

Secondary: 0.5x costs Sharpe 0.73; long-only Sharpe 0.76; pooled model Sharpe 0.61.

POST-HOC diagnostic (not pre-registered; does not change the verdict): is the edge just market drift?
Positions were 40-60% long, so drift explains only -3..+5 bp of the gross edge; the timing part is
+13 to +28 bp per trade for the six names above, with long days above each stock's average day AND
short days below it. TSLA (-9 bp) and RDDT (-13 bp, 70 trades) show no timing edge.
Reading: unlike every earlier test, this is a real, cost-surviving signal, but not strong or steady
enough to clear the bar set in advance. Next honest step: a forward paper test of this exact frozen
system on new data (separate pre-registration), not further cuts of this history.
Trial ledger: megacap-daily-2026-10-30. OOS predictions: reports/megacap_daily/oos_*.parquet.
