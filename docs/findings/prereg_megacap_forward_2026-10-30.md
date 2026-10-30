# Pre-registration: forward paper test of daily per-ticker models (2026-10-30)

Committed BEFORE the first forward decision (2026-11-02 15:50 ET). Uses only prices that do not exist
yet. Basis: `prereg_megacap_daily_2026-10-30.md` (historical FAIL by the frozen bar, Sharpe 0.63,
~+7%/yr, with a real timing edge of +13..+28 bp/trade in six names).

## What is tested
The daily system exactly as in `src/sgt/megacap/daily.py` + `walkforward.py` (LightGBM params,
10th/90th-percentile thresholds, monthly expanding-window retraining), on the six names that showed a
timing edge: AAPL, AMZN, GOOGL, MSFT, META, NVDA. (TSLA and RDDT are dropped; that choice was made on
historical data, which is exactly what this forward test checks.)

## Mechanics (virtual ledger, no orders)
- Monthly model: before the first decision of month M, each ticker's model is trained on all rows with
  session < M (1-minute SIP store and adjusted daily bars refreshed first). Model + thresholds saved to
  `reports/megacap_forward/models/YYYY-MM/`.
- Daily decision at 15:50 ET (launchd `local.sgt.megacap-daily`): features from adjusted daily bars
  through t-1 plus today's 1-minute bars ending 15:49 ET. Today's bars come from the IEX feed (the free
  real-time feed; the backtest used SIP, a declared train/serve difference). Decisions are written to
  `reports/megacap_forward/decisions/YYYY-MM-DD.json` with a timestamp, append-only (never overwritten).
  A decision written after 15:55 ET is void.
- Fill: market-on-close at the official close of t, exit at the official close of t+1 (adjusted daily
  bars), minus 4 bp per round trip. Equal capital per ticker; portfolio day = mean over the six.

## Evaluation
- Interim: monthly. Decision points: 2027-04-30 (~6 months) and 2027-10-29 (~12 months).
- Six months cannot confirm a Sharpe-0.6 strategy (too few days); it can only catch a broken one.
  At 6 months: CONTINUE if cumulative net > 0, else STOP.
- At 12 months: eligible for small real money (user decides, starting <= 10% of the account) only if
  cumulative net > 0, annualized Sharpe >= 0.5, and the mean daily net is within 2 standard errors of
  the backtest's +0.028%/day (i.e. not obviously a different strategy). Otherwise STOP.
- Stop at any time if cumulative net falls below -5%.
- Every monthly evaluation is recorded in the trial ledger (run id `megacap-forward-2026-10-30`).

## Amendment A1 (2026-10-30, before the first forward decision)
1. Laptop-friendly batch mode replaces the 15:50 real-time job (the user's Mac is not always on).
   `scripts/megacap_forward.py catchup` runs at login/wake and twice each weekday evening, and decides
   every session since 2026-11-02 that has no decision yet, from exactly the point-in-time data a 15:50
   decision would use: SIP 1-minute bars ending 15:49 (now SIP, not IEX - closer to the backtest),
   adjusted daily bars through t-1 and t's official open. Proof is reproducibility, not the clock:
   month models are trained only on rows whose labels were known before the month (session < M,
   last session embargoed), saved append-only with SHA-256 hashes checked on every load, and the
   decision code is tested point-in-time. The 15:55 void rule now applies only to real-time decisions.
   Verified 2026-10-30: a simulated missed week (2026-10-26..29) retrained October and reproduced the
   corrected backtest's decisions exactly (0 mismatches, predictions to 1e-12).
2. Benchmark for the 12-month check: the corrected backtest mean (+0.032%/day, Sharpe 0.74) replaces
   +0.028%/day.
3. Real-time execution (needed only when real orders go to a broker) moves to a cloud runner before
   any real money; it is not part of this paper test.

## Amendment A2 (2026-10-30 15:52 ET, before any forward decision exists)
Start moved from 2026-11-02 to 2026-10-30 (user: start as soon as possible). The 2026-10-30 decision is
made by the A1 batch job after 16:10 ET from the October models (trained on sessions < 2026-10-01, the
same models the corrected backtest used for October) and filled close 10-30 -> close 11-02. No forward
decision or forward price had been seen when this was written. Decision points unchanged.
