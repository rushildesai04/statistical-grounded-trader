# Pre-registration: a money score for the daily model's calls: expected dollars gate and size (2026-11-14)

Status: FROZEN before any number below is computed. Script `scripts/money_score_study.py`, run ONCE on training; the
held-out slice is opened once, only on a training PASS.

## Why (owner 2026-11-14)
"The confidence score must actually work based on how much money we think we are going to potentially make or lose
because that is the sole factor in deciding how much money to put in for trades and even executing trades in the
first place." Review of 2026-11-13 (PLAYBOOK section 5): the model went long AMD on a +1.25% forecast while AMD was
swinging 7.4% a day; the position rule compares a forecast with that stock's own past forecasts, never with its risk
today, and every call gets the same risk budget whatever the forecast. The stored "confidence" of the alert books is
100 x P(the average of similar trades is profitable): not money, and not used for size.

## Data
The walk-forward out-of-sample records of the 21 names (`reports/megacap_daily_v2`, `reports/megacap_oos`,
`reports/aaoi_daily`; columns `session, label, pred, position, d_rv21`): the same frozen records behind the three
forward tests. No new model, feature or training.
- Burn-in 2018-01-02 .. 2019-12-31: used only to start the calibration below.
- Training 2020-01-02 .. 2026-06-30. Held-out 2026-07-01 .. 2026-10-28.
- Honest note: these records have been looked at in aggregate (per stock, per side, per year), never by forecast size
  or by forecast against risk. Training is therefore weak evidence and held-out is short (about 84 sessions); the real
  proof stays the forward paper record.

## The score (everything is known at 15:50 ET of the decision day)
For a call on stock i, session t, side s (+1 long, -1 short), in calendar month M:
1. Calibration `b_M`: the slope of `label` on `pred` (least squares with an intercept) over ALL rows of all 21 names
   with session before the first day of M, clipped to [0, 1]. One number per month, never fitted on its own month.
   A slope <= 0 means the forecast carries no information: every expected value is then minus the cost (no trade).
2. Expected net return `EV = s x b_M x pred - cost` (cost 4 bp, RDDT 10 bp: the forward tests' round trip).
3. Risk today `sigma = d_rv21` (the stock's daily swing over the last 21 sessions).
4. Money per unit of risk `z = EV / sigma`, and `z_ref,M` = the median `z` of all calls with EV > 0 and session before
   the first day of M.
5. Size multiplier `m = 0` when EV <= 0 (no trade), else `z / z_ref,M` clipped to [0.25, 2].
The score shown for a trade is its expected dollars: weight x book value x EV.

## Books compared (long calls only: the owner's standing rule is longs first; long and short is reported, not decided)
- BASE (today's sizing, simplified): weight `min(25%, 1% / sigma)` per call, all weights of a day scaled down together
  when they sum above 100%. (The live books size on the 14-day range and cap portfolio beta; the study uses `d_rv21`
  and no beta cap for both books, so the comparison is like for like.)
- SCORE: BASE weight x `m`, same 25% and 100% caps.
- Day return = sum of weight x (s x label - cost). A day with no call returns 0 in both.

## Tests (one-sided, session-clustered bootstrap, 10,000 resamples, seed 20261114; Bonferroni 0.05 / 2 = 0.025 each;
## p = (1 + resamples with statistic <= 0) / 10,001: fail closed)
- T1, does the score rank calls: mean net per long call with `z` above `z_ref,M` minus mean net per long call at or
  below it (calls with EV <= 0 count as below). PASS: difference > 0 and p < 0.025.
- T2, does sizing by it pay: per session `D = k x R_SCORE - R_BASE`, where `k` = (mean gross of BASE) / (mean gross
  of SCORE) over the tested sessions, so both deploy the same dollars on average. PASS: mean D > 0 and p < 0.025.
- Minimum sample: 300 long calls in training, 40 in held-out; fewer = no verdict (treated as FAIL).
- Reported, never deciding: mean daily return, Sharpe, worst drawdown and per-year means of BASE and SCORE; the same
  two tests on long and short calls together; `b_M` and `z_ref,M` through time; share of calls with EV <= 0.

## Decision rule
- T1 and T2 PASS on training AND on held-out: ADOPT (owner's OK needed): the long-calls paper book (`megacap_long`,
  first session 2026-11-16) sizes by SCORE through an amendment to its pre-registration (its deciding ledger is equal
  weight and does not change); cards and texts show expected dollars from this score.
- T1 PASS on both, T2 not: the score is shown (expected dollars) and calls with EV <= 0 are not traded in the paper
  book; size stays BASE.
- T1 FAIL on training: the forecast size carries no usable money information. The expected dollars shown for a model
  call are then the tested average of calls like it (same stock, same side: `config/models/call_stats.json`) x size,
  and nothing about sizing changes. Held-out stays closed.
- Whatever the result: no change to the two long-and-short forward tests, no real money.

## Not in this test
Same-theme overlap of calls and earnings dates (the other two faults of 2026-11-13) need data this record does not
hold; they get their own pre-registration. The alert books' score is not a model forecast: it is their tested average
per trade (replaced by the forward record while the drift guard is in alarm), which is negative for every alert book
today, so they stay off.
