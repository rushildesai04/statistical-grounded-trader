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

## Result (run once, 2026-11-12; `reports/repeat_regime/result.json`) - both FAIL
| candidate | training | verdict |
|---|---|---|
| R1 longs on repeat alerts | A2 per dollar -1.78% (n 437) vs first-time -1.91% (n 1,805); diff +0.13%, p 0.20 | FAIL |
| R2 shorts after hot-tape days (prior session > 9 events) | not hot +2.90% (n 705) vs hot +1.72% (n 313); p 0.14; hot total +5.38 (skipping lowers the total) | FAIL |
Secondary: R2 on all no-news fades (F8 not applied) +2.17% vs +0.69% (p 0.064). Held-out stays closed.
Reading: multi-day runners are bad shorts (F8) but not good longs: the long book loses on repeats and first-time
alerts alike. Hot-tape days are weaker for shorts but still profitable, so skipping them gives up money.

## Validation of what runs live (descriptive, 2026-11-12)
- F8 by period (no-news, kept vs repeat): training A (to 2025-06) +3.03% vs -2.23%; training B +2.35% vs -0.53%;
  held-out +2.55% vs -1.33%; 2025 and 2026 both; price $2-5, $5-10, $10+ all +3% to +5% in favour. Repeats exactly
  one day back +1.73% (n 81) vs 2-3 days -1.84% and 4-7 days -3.18%: seen after the run, not acted on (the registered
  window stays 1-7 days; a forward question).
- A2 (longs) by period: +0.49% (training A), +0.37% (training B), +0.45% (held-out) per dollar vs the old rule.
- Drift guard replayed on history (CUSUM k 0.5 h 5 vs +1.70%, win rate p < 0.01): 0 alarms on 1,839 no-news fades;
  1 alarm on the 1,389 F8-kept fades (2026-09-24, a weak month: no-news September -0.19%). One halving in two years,
  in a weak stretch.
- Forward champion (Oct 30 - Nov 11, n 60): -2.77%; F8 would keep 54 at -3.00% and skip 6 at -0.70%: the recent
  weakness is not explained by repeats. The guard watches it.
