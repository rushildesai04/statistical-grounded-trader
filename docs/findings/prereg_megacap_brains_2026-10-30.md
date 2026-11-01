# Pre-registration: per-ticker learning models on mega caps (2026-10-30)

Committed BEFORE any model is trained or any feature is computed on these tickers' 1-minute data.
Nothing below may change after the run. One configuration, scored once.

## Hypothesis (the user's)
Each liquid large-cap stock has its own repeatable intraday patterns. A separate model per ticker
("a brain per ticker"), retrained every month on everything it has seen so far, can trade the next
30 minutes profitably after costs. Mega caps cost ~50-100x less to trade than the small caps
that failed before (tests 1-12), which is the reason to expect a different result.

## Universe and data
AAPL, AMZN, GOOGL, MSFT, META, NVDA, TSLA (2016-01-04 ..), RDDT (IPO 2024-03-21 ..).
Context: SPY, QQQ. Alpaca SIP 1-minute bars, regular hours only (09:30-16:00 ET), `data/alpaca`.

## Decisions and label
- Decision times: every 30 minutes, 10:00, 10:30, ..., 15:30 ET (12 per day).
- Label: return from the OPEN of the bar starting at decision time t to the OPEN of the bar starting
  at t + 30 min (15:30 -> the 16:00 close if no 16:00 bar: last close of the session).
- Features use only bars that ENDED at or before t (bar start <= t - 1 min). Point in time.

## Features (fixed list, `src/sgt/megacap/features.py`)
Own ticker: log returns over the last 1, 5, 15, 30, 60, 120 minutes; return since the 09:30 open;
overnight gap (open vs prior close); prior day's return; realized volatility of 1-minute returns over
30 and 120 minutes; volume of the last 30 minutes / the 20-day average volume for the same 30-minute
slot; distance to session VWAP; RSI(14) on 1-minute closes; (EMA9 - EMA21)/price on 1-minute closes;
distance from prior day's high and low. Market: SPY and QQQ returns over 5 and 30 minutes and since
open; own return since open minus SPY's. Calendar: minutes since open, day of week.

## Model ("brain") and learning schedule
- Per ticker: LightGBM regressor, fixed hyperparameters: 300 trees, learning rate 0.03, 15 leaves,
  min 200 rows per leaf, feature fraction 0.8, bagging fraction 0.8 every 1, seed 0. Target = label
  winsorized at the training set's 1st/99th percentiles.
- Walk-forward: for each calendar month M from 2018-01 to 2026-10, train on ALL of that ticker's
  rows before M (expanding window), predict month M. RDDT starts once it has >= 250 sessions.
- Thresholds (refit with the model): long if prediction >= the 90th percentile of the model's
  predictions on its own training rows; short if <= the 10th percentile; else flat.

## Trading and costs
- Each signal = one 30-minute trade (no netting across decisions), equal notional per ticker.
- Cost per round trip: 4 bp for the seven 2016 tickers, 10 bp for RDDT (spread + slippage,
  deliberately above typical mega-cap spreads). Net = position x label - cost.
- Portfolio daily return = mean over tickers trading that day of the sum of that ticker's net trade
  returns that day (each ticker gets 1/N of capital; one trade at a time per ticker).

## Decision criteria (portfolio, whole walk-forward 2018-01 .. 2026-10)
PASS only if ALL hold:
1. Mean daily net return > 0 with the 95% one-sided lower bound > 0 (day bootstrap, 10,000
   resamples, seed 20261030);
2. Annualized Sharpe of daily net returns >= 1.0;
3. Still positive mean at 2x costs;
4. Positive net in at least 6 of the 9 calendar years 2018-2026;
5. The OOS period from 2025-01 onward (the most recent) has positive mean net.
FAIL otherwise. Per ticker (reported, used only to choose which tickers continue IF the portfolio
passes): a ticker continues if its own one-sided lower bound > 0 at Bonferroni alpha 0.05/8.

If PASS: the passing tickers go to a forward paper test (live bot, separate paper account) for at
least 3 months before any real money, with the same frozen models retrained monthly.

## Secondary (reported, never used for the decision)
- One pooled model for all tickers (same features + ticker id) vs per-ticker models.
- Long-only version; costs at 0.5x; results by year, by decision time, hit rate, turnover.
- Information coefficient (rank correlation prediction vs label) per ticker per year.
Trial ledger run id `megacap-brains-2026-10-30`.

## Amendments (before any feature was computed or model trained)
### A1 (2026-10-30)
The 1-minute store is split-UNadjusted (`adjustment=raw`). Within-session features and the label are
unaffected. Cross-day inputs are therefore taken from Alpaca split/dividend-adjusted daily bars
(`adjustment=all`): overnight gap = adj open / prior adj close; prior day's return from adjusted
closes; prior day's high/low rescaled into today's raw price units by (today's first 1-minute open /
today's adjusted daily open). The volume feature uses DOLLAR volume (price x shares), which splits do
not change: last-30-minute dollar volume / mean of the same slot over the previous 20 sessions.
### A2 (2026-10-30, after the feature dry run, before any model was trained)
Half-day sessions (13:00 ET close) kept their thin 13:00-16:00 post-market bars as if regular hours, which would create trades
after the close. Fix: bars at or after the exchange-calendar close (XNYS) are dropped; no decision at or after the last regular bar; an exit that would fall after
it uses that bar's close. Found via a sanity check of feature ranges (dollar-volume ratio = 0); no
label, prediction or return had been examined.

## Result (scored once, 2026-10-30) — VERDICT: FAIL
Walk-forward 2018-01-02 .. 2026-10-29 (2,219 days), ~850 monthly per-ticker refits.
Portfolio: mean daily net -0.061% (lower95 -0.079%), Sharpe -1.91, ~-15%/yr; negative in all 9 years;
2x costs -0.172%/day; 2025+ -0.099%/day. All five checks failed.

| ticker | trades | gross per trade | hit | daily net | Sharpe | IC (mean by year) |
|---|---|---|---|---|---|---|
| AAPL | 6,175 | +0.43 bp | 51% | -0.099% | -1.99 | +0.017 |
| AMZN | 5,932 | +1.88 bp | 52% | -0.057% | -1.02 | +0.029 |
| GOOGL | 6,573 | +1.17 bp | 51% | -0.084% | -1.59 | +0.011 |
| MSFT | 5,885 | +1.10 bp | 51% | -0.077% | -1.57 | +0.019 |
| META | 6,546 | +0.23 bp | 50% | -0.111% | -1.79 | +0.007 |
| NVDA | 5,593 | +3.62 bp | 52% | -0.010% | -0.12 | +0.030 |
| TSLA | 5,831 | +4.41 bp | 52% | +0.011% | +0.12 | +0.020 |
| RDDT | 648 | +3.39 bp | 48% | -0.108% | -1.25 | +0.008 |

Secondary: 0.5x costs -0.005%/day (Sharpe -0.17); long-only Sharpe -1.25; one pooled model Sharpe
-1.42 (per-ticker was not better than pooled in any useful sense).
Reading: the models do learn something real but tiny - the prediction-to-outcome rank correlation is
positive for all 8 tickers, and gross edge is +0.2 to +4.4 bp per 30-minute trade - but that is below
even the 4 bp mega-cap round-trip cost. Per the criteria, nothing goes to paper. Any follow-up (e.g.
cheaper execution) must be a NEW pre-registration tested on new data, not a re-cut of this run.
Trial ledger: megacap-brains-2026-10-30. Out-of-sample predictions: reports/megacap/oos_*.parquet.
