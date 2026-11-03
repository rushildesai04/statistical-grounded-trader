# Pre-registration: fast learned models on large caps, 30 minutes to the close (2026-11-03)

Committed BEFORE any multi-horizon label, the new feature, or any model in this study is computed.
Study id `fast-megacap-2026-11` in the holdout vault (`config/holdout_registry.json`):
development 2016-01-04 .. 2023-12-29, held-out 2024-01-02 .. 2026-10-29. The scorer
(`scripts/fast_megacap_study.py score`) reads held-out rows only after the vault confirms this file and
the scorer are committed unchanged, once (unlock in the trial ledger, run `vault:fast-megacap-2026-11`).

## Why this test, and what is different from the failed 30-minute brains
User goal (2026-11-03): faster trades (minutes to hours) that make money, models that get smarter with
more data. The 30-minute per-ticker brains (`prereg_megacap_brains_2026-10-30.md`) FAILED, but every
ticker had a positive prediction-to-outcome rank correlation and gross +0.2 .. +4.4 bp per trade:
real skill, smaller than the 4 bp round-trip cost. This study attacks exactly that gap, with three
levers declared now and chosen on development data only:
1. Hold longer (60 min, 120 min, to the close): the move per trade grows with the holding time while the
   cost per trade does not.
2. Trade fewer, stronger signals (top/bottom 10%, 5%, 2% of the model's own training predictions).
3. A market-neutral cross-sectional book (long the 2 highest, short the 2 lowest predictions across
   the universe), which removes the market move that dominates single-name noise.
Plus one new feature with published support (Heston, Korajczyk and Sadka 2010, intraday periodicity):
each name's mean return over the same window on the previous 20 sessions.

## Universe and data
19 names: AAPL, AMZN, GOOGL, MSFT, META, NVDA, TSLA (brains names; RDDT dropped: IPO 2024, no
development history) + AVGO, NFLX, COST, JPM, V, MA, LLY, UNH, XOM, WMT, ORCL, AMD (the 12
unseen-at-the-time names of `prereg_megacap_oos_2026-10-30.md`). Context SPY, QQQ.
Alpaca SIP 1-minute bars, regular hours only (`data/alpaca`), split/dividend-adjusted daily bars for
cross-day inputs (brains amendment A1), early-close handling (brains amendment A2).

## Rows, features and labels
- Features: the brains feature list `sgt.megacap.features.FEATURES` computed by the unchanged
  `sgt.megacap.features.session_rows` (point in time: only bars ended by the decision), plus
  `slot_ret` = mean of this name's label for the same (decision minute, horizon) over the previous
  20 sessions (NaN until 20 exist).
- Horizons H and their non-overlapping decision minutes after 09:30 ET (one trade at a time per name):
  | H | decisions (ET) |
  |---|---|
  | 30 min | 10:00, 10:30, ..., 15:30 (12) |
  | 60 min | 10:00, 11:00, ..., 15:00 (6) |
  | 120 min | 10:00, 12:00, 14:00 (3) |
  | close | 10:00 (1) |
- Label: OPEN of the bar at the decision minute k to the OPEN of the bar at k + H, or the CLOSE of the
  last regular-session bar when k + H is past that bar (H = close always uses that close). Same rule as
  the brains label.

## Models (frozen; retrained every month on everything before that month = "learns with more data")
LightGBM, the brains hyperparameters `sgt.megacap.walkforward.LGB_PARAMS`, target winsorized at the
training 1st/99th percentiles, monthly expanding walk-forward from 2018-01, at least 250 training
sessions. One model per (name, H) for the per-name books; one pooled model per H (features + name id,
target = label minus the cross-sectional mean label of that decision) for the cross-sectional book.

## Configurations (16, all declared now)
- Per-name books (12): H in {30, 60, 120, close} x threshold q in {0.90, 0.95, 0.98}. Long when the
  prediction is >= the q-quantile of the model's predictions on its own training rows, short when
  <= the (1 - q)-quantile, else flat.
- Cross-sectional books (4): H in {30, 60, 120, close}; at each decision minute long the 2 names with the
  highest pooled prediction, short the 2 lowest (names with a missing prediction are skipped; fewer than
  8 names with predictions = no trade).
- Costs: 4 bp per round trip per name traded (each leg of the cross-sectional book pays 4 bp).
- Capital: per-name books give each of the 19 names a fixed 1/19 of capital (a name with no trade
  earns 0; stricter than the brains convention of dividing among names that traded). The cross-sectional
  book splits capital 4 ways at each decision. Daily return = sum over that day's decisions.

## Development and selection (development data only, 2018-01 .. 2023-12 predictions)
All 16 configurations are run once on development data and reported. The selected configuration is the
one with the highest annualized Sharpe of daily net returns, among configurations whose mean daily net
is also > 0 at 2x costs. If no configuration has a development Sharpe >= 0.5, the study STOPS at
development: the vault is NOT unlocked, the held-out period stays clean for a future idea, and the
verdict is FAIL (development).

## Held-out decision (the selected configuration only, held-out 2024-01-02 .. 2026-10-29, scored once)
The walk-forward continues through the held-out months exactly as live (each month's model trains on
everything before it). PASS only if ALL hold:
1. Mean daily net > 0 with a 95% one-sided lower bound > 0 (day bootstrap, 10,000 resamples, seed 20261103);
2. Annualized Sharpe of daily net >= 1.0;
3. Mean daily net > 0 with costs doubled;
4. At least 2 of the 3 calendar years 2024, 2025, 2026 (to 10-29) have positive net.
FAIL otherwise. PASS -> forward paper record on the Fly runner (intraday, ARM-gated like the alert fade,
virtual book first) for at least 3 months before any money; monthly retrain with a champion/challenger
gate (separate design). FAIL -> fast directional trading on these large caps is closed; no re-cut of
this data.

## Secondary (reported, never used for the decision)
All 16 configurations on the held-out period at 4 bp and 2x costs, printed by the same scoring run
only after the verdict has been written to the trial ledger.

## Contamination statement (what was already seen in the held-out period before this file)
- The 7 brains names: 30-minute walk-forward results 2018-2026 (FAIL), including 2024+.
- The 12 OOS names and the 6 daily names: close-to-close daily model results including 2024+.
- No multi-horizon intraday label, the `slot_ret` feature, any cross-sectional intraday book or any
  threshold other than 0.90 has been computed for any name in any period.
The 30-minute q = 0.90 per-name configuration is close to the brains test on 7 of the 19 names; its
held-out result is therefore partly known (negative). It stays in the grid for completeness; if it
were selected on development data, the held-out test would still run but the report would flag it.

Trial ledger run id `fast-megacap-2026-11-03`.

## Result (2026-11-03, scored once; vault unlock `vault:fast-megacap-2026-11`, trial ledger `fast-megacap-2026-11-03`) — VERDICT: FAIL
Development (2018-01 .. 2023-12, 1,509 days): best configuration per-name, hold to the close, q = 0.95:
+1.2%/yr, Sharpe 0.52 (just above the 0.5 stop line), so the held-out test ran.
Held-out 2024-01-02 .. 2026-10-29 (710 days), selected configuration: mean daily net -0.0019%
(-0.5%/yr), lower95 -0.0087%, Sharpe -0.27, 2x-cost -0.0052%; years 2024 -1.1%, 2025 -0.6%,
2026 +0.4%. All four checks failed.

Gross edge per trade, development vs held-out (per-name books; from `reports/fast_megacap/wf_*.parquet`):

| H | q 0.90 | q 0.95 | q 0.98 |
|---|---|---|---|
| 30 min | +1.3 / +0.6 bp | +1.6 / +0.4 | +2.5 / +1.0 |
| 60 min | +2.0 / +1.7 | +2.9 / +2.3 | +4.5 / +5.1 |
| 120 min | +1.8 / +2.0 | +2.2 / +2.5 | +4.1 / +0.9 |
| close | +4.9 / -1.1 | +8.6 / +1.7 | +10.6 / +4.7 |

Reading: longer holds and stricter thresholds raised gross edge per trade on development data as
intended, but the best cells shrank by 60-80% on held-out data and stayed at or below the 4 bp
cost. Every cross-sectional book lost money in both periods. The models still have some skill
(gross is positive in 11 of 12 per-name cells held-out), but it is too small to pay for trading. Fast
directional trading on these 19 large caps is closed per this pre-registration; no re-cut of this data.

Reporting note: the scorer's SECONDARY table printed trade counts and gross per trade over the whole
2018-2026 walk-forward, not the held-out period alone (net returns, Sharpe and the verdict were held-out
only and are correct). The table above is the corrected split; the scorer is left unchanged as the
committed, unlocked version.
