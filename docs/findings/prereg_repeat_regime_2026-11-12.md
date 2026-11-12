# Pre-registration: longs on repeat alerts (R1) and shorts after hot-tape days (R2), 2026-11-12 round 2

Status: FROZEN before any outcome is computed. Script: `scripts/repeat_regime_study.py`, run ONCE.

## Why
Owner 2026-11-12 after merging #96-#98: "keep iterating and making this better through validation". F8 showed repeat
alerts are multi-day runners where shorts lose (prereg_fade_repeat_days_2026-11-12). Two follow-ups:
- R1: if repeats keep running, the LONG book may do better on them than on first-time alerts.
- R2: the forward short record (-3.00% on 54 F8-kept trades, Oct 30 - Nov 11) is weak; one candidate cause is a hot
  tape: the day after the channel fires many alerts, squeezes may be stronger.

## Data (no new choices)
Events, nets, costs and splits exactly as prereg_fade_repeat_days (fades: `reports/fade_news/*_events.csv`) and
prereg_long_risk (longs: `reports/long_risk/base.csv`, A2 per-dollar net = w x e_net with w = min(1, 2.68% / d)).
Repeat = the same symbol has a fade event 1-7 calendar days earlier (`fade_repeat_days_study.mark_repeats`).
Prior-session count = the number of fade events (first alerts per symbol-day) on the previous NYSE session
(`default_calendar`), 0 if none. HOT = prior-session count above the 75th percentile of per-session event counts over
training sessions (sessions with >= 1 event; outcome-blind: 9 events).

## Candidates (Bonferroni alpha 0.05 / 2 = 0.025, one-sided)
- **R1 long repeats**: A2 per-dollar net of longs on repeat alerts minus A2 per-dollar net of longs on first-time alerts
  (training, sessions resampled jointly, 10,000 resamples, seed 20261112). Minimum 100 repeat longs. A pass means:
  keep longs on repeat alerts only (skip first-time alerts), adopted only if held-out statistic > 0 (n >= 30).
- **R2 hot tape**: on no-news, F8-kept training fades: mean net when NOT hot minus mean net when hot; minimum 100 hot;
  and the hot trades' total <= 0 (skipping must raise the total). A pass means: skip shorts on hot days, adopted only
  if held-out statistic > 0 (n >= 30) and the hot held-out total <= 0.
Live adoption of either needs the owner's OK. PROFITABLE label for R1 as in prereg_long_risk.

## Not done
No other windows, thresholds or regime measures (index trend, breadth). Secondary only: R2 on all no-news fades
(F8 not applied); the counts above.
