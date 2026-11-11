# Pre-registration: let half of a winning long run (owner's GEVO note, 2026-11-11)

Status: FROZEN before any run. Script: `scripts/long_runner_study.py`, run ONCE. Owner decision 2026-11-11 (multiple
choice): "Yes, run the test".

## Idea (owner's note on GEVO, long $1.92 -> $1.96 target, 2026-11-11)
"Sell part at the target and let the remainder run, exiting when price breaks the latest pullback low." The long
book sells everything at the 2R target, so a winner that keeps going is capped. Every long exit tested so far was a
target or a timer (1.5R / 2R / 3R, 15 / 30 / 60 minutes; prereg_long_setups_2026-11-10); a trailing runner is untested.

## Population (the live long book on past alerts, no new choices)
Events: the long_setups events (reports/fade_news dev + holdout alerts with their research start bar
`reports/telegram/trades_pooled_first.csv`, plus the halt-reopen events `reports/fade_halt_audit/halt_events.csv`),
1-minute RTH bars `data/alpaca`. Per event the live book's construction (`long_book.first_fill`): setups as live
(`long_shadow.setups`: L4a/L4b + bull flag v2 + L6; L7a/L7b after a halt), each simulated by `patterns.simulate`
(with the fill-bar target fix, amendment A2 of prereg_long_setups), the FIRST to fill (earliest entry bar; ties in
`long_book.LONG_KINDS` order). Costs: the event's round-trip `cost_rt` (no locate: longs). Splits: training =
sessions before 2026-07-01, held-out = from 2026-07-01 (the same D_STAR as prereg_long_setups).

## Exits
- **BASE** (the live rule): stop first, then the 2R target, else the close of the last bar within 30 minutes.
- **R1** (primary, the owner's idea): identical to BASE until the 2R target trades. Then half sells at the target and
  the other half (the runner) gets a trailing stop = the larger of the setup stop and the LOW of the most recent
  confirmed swing low (a bar whose low is below the lows of the 2 bars before and the 2 bars after it, confirmed when
  the 2nd bar after it has closed; only swing lows at or after the entry bar). The trail only moves up. The runner
  sells when a bar trades at or below the trail (fill at the lower of the trail and the bar's open), else at the
  session's last bar close. On the target bar itself, a low at or below the trail sells the runner at the trail
  (conservative: order inside the bar unknown). No 30-minute limit on the runner. Trades that never reach the target
  are identical to BASE.
- Secondary (reported, never decide): **R_ALL** (the whole position runs after the target), **R1_60** (R1 with the
  runner sold at the close of the last bar within 60 minutes of the entry), **TRAIL** (no target, no timer: the whole
  position trails from the entry with the same swing-low rule, sold at the trail or the session close).
R1 - BASE = half of (runner - target) on target trades only, so the test is exactly "does the move continue past 2R
by more than the trail gives back".

## Decision (fixed now)
Statistic: paired per-trade difference R1 - BASE (net), session-clustered bootstrap (10,000 resamples, seed 20261111),
one-sided p = share of resampled means <= 0.
- **ADOPT R1 as the live paper long exit** only if training mean difference > 0 with p < 0.05, AND the held-out mean
  difference > 0 (held-out opened only if training passes). Adoption still needs the owner's OK and a prereg amendment.
- **PROFITABLE** (a separate label, also needed before any real money): R1 training mean net > 0 with the bootstrap
  lower 95% bound > 0, AND held-out mean net > 0. Otherwise the long book stays a losing measurement whatever the exit.
- Anything else: FAIL, recorded in the PLAYBOOK section 4.
Minimum: 100 training target trades, else the test is under-powered and recorded as such (no adoption).

## Not done
No parameter search (2R target, 2-bar swing lows and the half split are the owner's words or the live book's existing
values). Held-out is opened once, only for a training pass.

## Result (run once, 2026-11-11; `reports/long_runner/result.json`) - FAIL
Training (sessions before 2026-07-01): the live book's construction gives **2,242 trades, BASE -2.29% per trade**
(matches the long book's known first-fill figure), 337 of them reach the 2R target.
| exit | training mean net per trade |
|---|---|
| BASE (live: stop, 2R target, 30 min) | -2.29% |
| **R1 (half at target, half trails the latest swing low)** | **-2.42%** (difference -0.13%, p 0.97; win 15%) |
| R_ALL (whole position runs after the target) | -2.55% |
| R1_60 (runner capped at 60 min) | -2.41% |
| TRAIL (no target, trail from entry) | -2.40% |
On the 337 target trades the 2R exit earned +8.78% net; the runner earned +7.08%: 336 of 337 runners were sold by the
trail (the next pullback low), giving back more than the continuation added. Training fails, so held-out stays
closed. Not adopted; the long book keeps its 2R target. The book loses before the exit (only 15% of trades reach
2R), so an exit change cannot rescue it.
