# Pre-registration: institutional flow ("wash" = VWAP, FINRA short volume, 13F breadth) with learned entry/exit/stop (2026-11-02)

Committed BEFORE the training-period rule search (cv) is run and BEFORE any held-out data is read.
Study id `inst-flow-2026-11` in the holdout vault (`config/holdout_registry.json`); the scorer
(`scripts/inst_flow_study.py score`) can read 2023+ only after the vault confirms this file and the
scorer are committed unchanged, once (unlock recorded in the trial ledger, run `vault:inst-flow-2026-11`).

## Why
The trader's claims, as clarified by the user on 2026-11-02: institutions keep buying specific
tickers and "wash" their orders through VWAP; each ticker differs; beta and time of day matter;
entry, exit and stop rules should be LEARNED (the "brain"), not hand-written. Earlier hand-written
VWAP rules on 8 names were negative on training (prereg_aaoi_intraday). This test lets a model learn
from VWAP-execution footprints, off-exchange short-volume share, and 13F holder breadth across the
whole liquid US market, with per-ticker differences entering through the features (beta, volatility,
liquidity, the ticker's own flow history) rather than one model per ticker (too little data per name).

## Data and point-in-time rules
- Daily bars, adjusted (splits + dividends), active AND delisted symbols (data/daily, Alpaca SIP),
  extended to 2026-10-30. Includes the daily VWAP.
- FINRA Reg SHO daily short-sale volume (off-exchange, FINRA-reported). Day t's file is published
  after t's close: used as a feature of the close of t; trades enter at the open of t+1.
- SEC Form 13F data sets: per CUSIP and report period, number of distinct filers holding (13F-HR
  originals, shares only, no options), filings on or before period end + 45 days; available from the
  first session after that deadline. CUSIP -> ticker via OpenFIGI (unmapped CUSIPs -> missing 13F
  features; LightGBM handles missing values).
- Universe each date t: close >= $5, 20-day median dollar volume >= $20M, 252 sessions of history,
  finite beta and ATR. ETFs are not excluded (no point-in-time asset-type field); stated limitation.

## Features (all known at the close of t; cross-sectionally ranked to [0, 1] per date before the model)
- VWAP ("wash"): close/VWAP - 1 (1 day, 5-day mean), share of the last 20 days closing above VWAP,
  20-day volume-weighted close-location value, 5-day / 60-day volume.
- FINRA: short-volume share (1 day, 5-day mean), 60-day z-score of the 5-day share, FINRA-reported
  volume / consolidated volume (5-day mean, capped 1.5).
- 13F: change in holder count vs the prior period, (new - exited holders) / prior holders, change in
  total institutional shares.
- Base controls: Blume beta (252 days vs SPY), 20-day volatility, ATR(14)%, 12-1 month momentum,
  5-day reversal, log 20-day median dollar volume, last-day return.

## Learned rule (the "brain"; selected on TRAINING data only)
- Model: LightGBM regressor, fixed hyper-parameters (200 trees, learning rate 0.05, 31 leaves,
  min 500 rows per leaf, 80% feature and row subsampling, seed 20261102). Target: the beta-hedged
  return from the open of t+1 to the close of session t+h, ranked per date.
- Rule grid (48): holding period h in {1, 5, 10, 20} sessions; entry = top q of the predicted
  cross-section (and bottom q for long-short), q in {5%, 10%}; stop in {none, 2 x ATR%, 3 x ATR%}
  from entry (intrabar breach fills at the stop, or at the open when the open is already through it);
  side in {long-short, long top-q hedged with SPY by beta}.
- Position weights within each daily cohort proportional to 1 / ATR% (each side sums to half the
  gross for long-short), every position beta-hedged with SPY. Costs: 10 bp per side; shorts pay 1%/year borrow.
- Selection: walk-forward over validation years 2019, 2020, 2021, 2022 (each model trained on all
  earlier training years, targets purged h+1 sessions before the validation year); choose the rule
  with the highest mean annualized Sharpe of daily cohort returns across the four years. Done
  separately for the FULL feature set and the BASE-only set. All 384 cv cells go to the trial ledger
  (run `inst-flow-2026-11-02-cv`).
- Final models: refit on all training rows (2016-01-04 .. 2022-12-30; forward paths computed on the
  training frame only, so no target uses a 2023+ price), frozen, applied to every held-out date.

## Tests on held-out 2023-01-03 .. 2026-10-30 (family of two, alpha 0.05/2 each, one-sided)
Statistic: daily cohort return (cohort net return / h), moving-block bootstrap (block 20 dates,
10,000 draws, seed 20261102), 2.5% quantile.
- T1 (tradeable, FULL model with its selected rule): PASS needs lower bound > 0, mean > 0 in both
  halves of held-out (by time), and no single calendar year > 50% of the positive yearly sum.
- T2 (does institutional flow add value?): daily difference FULL minus BASE (each with its own
  selected rule) on common dates; PASS needs the lower bound of the mean difference > 0.
- Secondary, reported only: Sharpe, per-family ablations, per beta tercile, long vs short legs,
  AAOI and its peers' contribution.

## What the verdict means
- T1 and T2 PASS: institutional flow, as learned, is a tradeable edge -> forward paper test on Fly
  through the approved book (SMS approval), sized by sgt.risk.sizing.
- T1 PASS, T2 FAIL: the model works but the flow data adds nothing beyond momentum/volatility/beta;
  the "institutions" part of the claim is not supported.
- T1 FAIL: rejected; the learned rules do not survive costs out of sample.

## Known limitations (stated before scoring)
FINRA volume is off-exchange only; 13F is quarterly with a 45-day delay and long positions only;
CUSIP mapping misses some delisted names; ETFs are in the universe; costs are flat (no per-name
spread model); borrow is a flat 1%/year (hard-to-borrow names cost more).

## Amendment A1 (2026-11-02, before the panel, cv or any held-out read)
Data as ingested (commit 0b777cb): FINRA consolidated (CNMS) files exist only from mid-2018; earlier
dates sum the Nasdaq (FNSQ) and NYSE (FNYX) TRF files, so the level of the FINRA features shifts at
the switch. Features are ranked per date before the model, which removes cross-date level shifts;
the 60-day z-score is affected for ~60 sessions around the switch (training period only). 13F: the
deadline is rolled forward to the next business day (a literal P+45 cut-off excluded 15-25% of
on-time filers in weekend quarters); new/exited holders count continuing filers only. CUSIP->ticker
coverage is reported with the panel. No rule, feature, grid, or test changes.

## Result (2026-11-02, scored once; vault unlock at commit 46ce73f, trial ledger `inst-flow-2026-11-02`) — VERDICT: T1 FAIL, T2 PASS -> rejected as a strategy

Data: 4.15M eligible stock-days (median 1,645 stocks/day); FINRA coverage 97%, 13F 85% (CUSIP map 39% of
CUSIPs, 78% of holder weight). Training cv (2019-2022 validation): FULL selected h=20, q=5%, no stop,
long-short (mean cv Sharpe 2.5; 2020 negative); BASE selected h=20, q=10%, 2xATR stop (-0.26).

| Held-out 2023-01 .. 2026-10 (941 cohort days) | mean / holding day | Sharpe* | halves | max-year share | 2.5% lower bound | verdict |
|---|---|---|---|---|---|---|
| T1 FULL (flow + base) | +1.93 bp (~+4.9%/yr net) | 2.92 | +3.68 / +0.19 bp | 64% | -0.0003 bp | **FAIL** (bound <= 0, year share > 50%) |
| BASE only | -0.52 bp | -0.98 | +0.61 / -1.64 bp | | -2.05 bp | |
| T2 FULL - BASE | +2.45 bp | | | | **+1.11 bp** | **PASS** |
*Sharpe of overlapping 20-day cohorts is inflated; the verdict uses the 20-day block bootstrap.

Secondary (reported only; a declared second vault read): VWAP + base -0.01 bp/day (no signal);
FINRA + base +1.58 (2023 +2.9, 2024 +4.1, 2025 -0.5, 2026 -0.9); 13F + base +0.73; long leg alone
(long top 5% hedged) -0.90 bp/day -> the profit came from SHORTING names the flow data flags, not from
buying what institutions buy; by beta tercile: low +0.83, mid +0.32, high +5.50 (halves +10.1 / +0.9).

Reading: institutional flow data (mainly FINRA off-exchange short volume, a little 13F breadth) carries
real cross-sectional information (T2), but the learned trade did not clear costs robustly out of sample,
decayed to ~zero in 2025-26, depends on the short side (real borrow on these names is above the 1%/yr
assumed), and the VWAP "wash" footprint adds nothing. Not promoted; no forward test.
