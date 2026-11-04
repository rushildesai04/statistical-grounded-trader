# Pre-registration: fade challengers in shadow — entry delay and holding time (2026-11-04)

Committed BEFORE any number in this file is computed. Plan: `docs/research/plan_2026-11-04.md` W4.

## Why
1. The live book does not enter when the rule was tested. The tested rule shorts at the first 1-minute bar at or
   after alert + 10 s. Under the owner's opt-out approvals (amendment F4) an unanswered request enters at
   alert + 4 min 20 s, and a quick OK at roughly alert + 1-2 min. Whether that delay costs money has never been
   measured. The delay challengers measure it on history; plan W5 (`sgt.fast.realism`) measures it live.
2. Holding time was only explored at 15 and 60 minutes (prereg_alert_fade_2026-11-01). Stops (10/20/30%) were
   explored on training and 20% was chosen for risk; stops are NOT re-tested here.

## Challengers (fixed; the champion is the frozen rule: delay 0, 60 min, 20% stop)
| id | entry | hold | stop |
|---|---|---|---|
| D2 | first RTH bar at or after the tested entry bar + 2 min | 60 min | 20% |
| D4 | first RTH bar at or after the tested entry bar + 4 min (about the opt-out auto-entry) | 60 min | 20% |
| H30 | tested entry | 30 min | 20% |
| H90 | tested entry | 90 min | 20% |
All holding windows contain RTH bars only (a window that crosses the close ends at the last RTH bar). Same
events (`reports/telegram/trades_pooled_first.csv`, status ok), same cover function
(`alert_fade_test.cover`), same costs (the event's `cost_rt` + 0.5% locate). A delayed entry with no RTH bar left
is no trade (net 0 for that event). Code: `sgt.fast.challengers`; the champion computed by it must equal
`alert_fade_test.score` exactly (unit test).

## Development gate (training events 2025-01-03 .. 2026-06-30 only)
Metric: total net per session (sum of net over the session's events). For each challenger, paired daily
difference vs the champion, session bootstrap (10,000 resamples, seed 20261104), one-sided lower bound at
alpha = 0.05 / 4 (Bonferroni over the four challengers, quantile 0.0125).
- PASS (goes to shadow as a candidate replacement): lower bound > 0, AND its 1st-percentile per-trade net is no
  worse than the champion's minus 5 percentage points (no buying mean with tail risk).
- Every challenger is shadowed regardless (forward data is cheap and the delay ones double as a live-design
  check), but only a PASS can ever be promoted.
- Diagnostic, decided now: if D4's mean daily difference vs the champion is below -0.5% of a trade per session
  AND its upper bound (quantile 0.9875) is < 0, the opt-out auto-entry is costing money; I bring the owner a
  redesign decision (e.g. enter at the alert and let NO exit early). Nothing changes automatically.

## Forward (shadow) and promotion
Nightly on the runner after the forward record (`fadeforward` job): every forward event (same as amendment F1)
is scored by the champion and every challenger from official SIP bars; results in
`reports/fade_challengers/shadow.csv` and one digest line. At the F1 decision point (n >= 300 forward events): a
dev-PASS challenger is RECOMMENDED for promotion only if its paired daily difference vs the champion on forward
data has a session-bootstrap lower bound > 0 at alpha = 0.05 / (number of dev-PASS challengers). Promotion changes
the live rule and therefore needs the owner's OK and a prereg amendment; it is never automatic.

## Secondary (reported only)
Held-out 2026-07..10 (already examined once for the champion) per challenger; win rate, mean per trade, worst 1%.
Trial ledger run ids `fade-challengers-2026-11-04-dev`, `fade-challengers-shadow`.
