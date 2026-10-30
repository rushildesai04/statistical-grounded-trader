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
