# Pre-registration: the bull flag trade with the owner-graded detector v2 — 2026-11-10

Committed BEFORE any v2 trade result is computed. The v1 bull flag (L5 long / S2 failed-flag short) FAILED
(`prereg_long_setups_2026-11-10`), but the owner then graded 18 charts: only 6 of 12 v1 detections were real bull
flags and one real flag was missed. So v1 tested a noisy stand-in for the trader's pattern. v2
(`sgt.fast.patterns.find_flag_v2`, frozen at this commit) adds four rules, each from a graded failure: pole >= +10%,
pole traded in >= 90% of its minutes, pole top = a new high of day, flag lighter than the last 5 pole bars. On the 18
graded charts v2 keeps all 6 real flags, finds the missed one and drops the 4 non-flags (fitted there: a fresh sample of
23 v2 charts is out for the owner's grading).

## Tests (two; Holm at family alpha 0.05)
- **L5v2** long: buy-stop at the v2 flag high (live 5 min), stop the flag low, target 2R, exit by 30 min (as L5).
- **S2v2** short: sell-stop at the v2 flag low, stop the flag high, target 2R, 30 min, + 0.5% locate (as S2).
Population, start bar, fills, costs, last entry minute: exactly `prereg_long_setups_2026-11-10` (same simulator).
Training 2025-01-03..2026-06-30: one-sided session-bootstrap p (10,000, seed 20261112) on mean net > 0, n >= 50.
PASS -> held-out 2026-07-01..10-29 CONFIRMED if mean > 0, p < 0.05, n >= 20. Note: the held-out months were seen for
v1's L5/S2 results (-1.55% / -0.74%); v2 picks a different set of events, but the confirmation is not fully blind, so a
pass goes to the paper book and the 300-trade forward proof before anything else.
Trial ledger `flag-v2-2026-11-10`. Secondary: the same events under v1's exits at 15/60 minutes and 1.5R/3R.

## Result (2026-11-10, run once, trial ledger `flag-v2-2026-11-10`) — both FAIL
| test | v2 flags (training) | fills | mean net | gross | win | p | v1 (for reference) |
|---|---|---|---|---|---|---|---|
| L5v2 long breakout | 756 | 199 | **-2.05%** | -1.14% | 29% | 0.9998 | -1.72% (n 247) |
| S2v2 failed-flag short | 756 | 556 | **-1.85%** | -0.42% | 35% | 1.00 | -1.38% (n 658) |
Every secondary is negative (15/60-min holds -1.5% to -1.9%, 1.5R/3R -1.7% to -2.0%). Held-out not opened (no pass).
Reading: a bull flag that matches the trader's eye (all 6 graded real flags, the missed one found, the 4 non-flags
dropped) does not make money on these alerts either; its breakouts fail slightly more often than v1's. The detector
stays at v2 for any future use; nothing changes in trading. PLAYBOOK section 4.
