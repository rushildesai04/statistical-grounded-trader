# Pre-registration: hold the fade longer — 2 hours and to the close (2026-11-05)

Committed BEFORE any number in this file is computed.

## Why
The fade's documented cause is retail attention: the most-bought small caps fall over the following days
(Barber, Huang, Odean, Schwarz, "Attention Induced Trading and Returns"). Our own results are monotone in holding
time: 15 min +0.18%, 30 min +0.65%, 60 min +0.97%, 90 min +1.14% per trade on training
(prereg_alert_fade_2026-11-01, prereg_fade_challengers_2026-11-04), and every exit that covers early earns less
(prereg_fade_exits_2026-11-05). Two longer same-day holds have never been tested. Overnight holds are out of scope:
a short held past the close pays the borrow fee, and we have no history of hard-to-borrow fees to price it.

## Challengers (fixed; the champion is the frozen rule: 60 min, 20% stop)
Same events, entries, stop and costs as the champion (`reports/telegram/trades_pooled_first.csv`, status ok; event
`cost_rt` + 0.5% locate), simulated by `sgt.fast.exits.simulate` (no signal, time cap only; RTH bars only, so a cap
past 16:00 ends at the last regular-session bar).

| id | hold |
|---|---|
| H120 | 120 minutes |
| H-CLOSE | to the last regular-session bar of the day (cap 480 minutes) |

## Development gate (training events 2025-01-03 .. 2026-06-30 only; run once)
Same as prereg_fade_exits: total net per session; paired daily difference vs the champion over events both legs
traded, session bootstrap (10,000 resamples, seed 20261105), one-sided lower bound at alpha = 0.05 / 2.
PASS needs lower bound > 0 AND a 1st-percentile per-trade net no worse than the champion's minus 5 points.
The champion computed here must equal `alert_fade_test.score` exactly (guard).

## Held-out and forward
Held-out 2026-07..10 was used once for the champion: SECONDARY only. Both run in shadow nightly on forward events
(`fadehold` job). At the forward decision point (n >= 300) a dev-PASS hold is RECOMMENDED only if its forward paired
daily difference has a session-bootstrap lower bound > 0 at alpha = 0.05 / (number of dev-PASS holds). A longer live
hold needs the owner's OK and a prereg amendment; never automatic.

## Secondary (reported only)
Mean / win / 1st percentile; stop share; median minutes held; per entry hour (09:30-11, 11-14, 14-16 ET).
Trial ledger run id `fade-hold-2026-11-05-dev`.
