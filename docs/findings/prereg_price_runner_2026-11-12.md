# Pre-registration: skip shorts on price-based multi-day runners (R3), 2026-11-12 round 3

Status: FROZEN before any outcome is computed. Script: `scripts/price_runner_study.py`, run ONCE.

## Why
F8 (prereg_fade_repeat_days) showed shorts lose on stocks the CHANNEL alerted in the prior 7 days: multi-day runners.
A stock can run for days without a channel alert. R3 asks whether a price-based runner measure adds to F8 on the
shorts the live book now takes (no-news, F8-kept).

## Data (no new choices except the measure below)
Fades and splits as prereg_fade_repeat_days (`reports/fade_news/*_events.csv`; net = 20% stop, 60 min, real costs;
dev = training, holdout from 2026-07-01). Population: no-news fades that F8 keeps (not repeats).
Run measure `ret5` = split-adjusted close of the last session before the alert's session / close 5 sessions earlier
- 1, from Alpaca 1Day bars with adjustment=split (`scripts/fetch_adjusted_daily.py` ->
`reports/adjusted_daily/bars.parquet`; the local data/daily bars are not split-adjusted). Unknown (fewer than 6 prior
sessions) = kept. Outcome-blind counts: ret5 known for 1,004 of 1,018 training and 369 of 371 held-out fades.

## Candidate (one; alpha 0.05 one-sided)
**R3**: skip a fade when ret5 > Q75, Q75 = the 75th percentile of ret5 over the training population (outcome-blind:
+14.2%; 251 training and 55 held-out fades above it). Statistic: mean net of kept minus mean net of skipped training
fades, sessions resampled jointly (10,000 resamples, seed 20261112). Minimum 100 skipped; skipped total <= 0.
ADOPT (live fade book, owner's OK) only on a training pass AND held-out kept - skipped > 0 with >= 30 skipped and the
held-out skipped total <= 0.
Secondary (never decides): the same on all no-news fades (F8 not applied); the correlation of ret5 > Q75 with the F8
repeat flag.

## Not done
No other lookbacks (1, 3, 10, 20 days) or thresholds; no sizing variant.

## Result (run once, 2026-11-12; `reports/price_runner/result.json`) - FAIL
Training, F8-kept no-news fades: kept (ret5 <= +14.2%) +2.49% (n 767) vs runners +2.69% (n 251); diff -0.20%,
p 0.57; runners' total +6.76 (skipping them would lose money). Held-out stays closed.
Secondary: on ALL no-news fades (F8 not applied) runners lose (+0.37% vs +2.48%, p 0.011), and 77% of the price
runners are F8 repeats. Reading: F8 already captures the multi-day-runner effect; what predicts a bad short is the
channel alerting the stock again, not the price run itself. Once F8 is applied, price runs add nothing.
