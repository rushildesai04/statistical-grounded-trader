# Pre-registration: fade exits that read the tape (2026-11-05)

Committed BEFORE any number in this file is computed.

## Why
The owner (2026-11-05): a fixed 60-minute exit is "an alarm clock"; once short, the agent should watch the trend
and cover when the move is over. Holding time was explored at 15 / 30 / 60 / 90 minutes
(prereg_alert_fade_2026-11-01, prereg_fade_challengers_2026-11-04): 15 and 30 are worse, 90 is indistinguishable
from 60. No exit that reacts to price after entry has been tested. The owner also asked where the 9/21 EMA lines
are: they belonged to the trader-rules study (`sgt.decision.trader_rules`), not to the fade, and are tested here as
an exit for the first time.

## Challengers (fixed; the champion is the frozen rule: 60 min, 20% stop)
All keep the 20% stop and the champion's events, entries and costs (`reports/telegram/trades_pooled_first.csv`,
status ok; event `cost_rt` + 0.5% locate). Signals are read at a 1-minute bar's CLOSE and acted on at the NEXT
bar's OPEN (no look-ahead); with no next bar inside the cap, the cover is that bar's close. Within a bar the stop
is checked first (worst case). Indicators use the session's RTH 1-minute SIP bars up to and including the signal
bar (VWAP from 09:30; EMAs `ewm(span, adjust=False)` on RTH closes). Holding windows hold RTH bars only.

| id | exit signal | time cap |
|---|---|---|
| X-TRAIL | once the short has gained >= 5% (a bar low <= 0.95 x entry), cover when a close is >= 5% above the lowest low since entry | 120 min |
| X-VWAP | after a close below VWAP, cover at the first close back above VWAP | 120 min |
| X-EMA | cover when EMA9 crosses above EMA21 (EMA9 > EMA21 at the close, <= at the previous close) | 120 min |
| X-TGT | take profit: a bar low <= 0.85 x entry covers at min(0.85 x entry, that bar's open) | 60 min |

## Development gate (training events 2025-01-03 .. 2026-06-30 only; run once)
Identical to prereg_fade_challengers: metric total net per session; paired daily difference vs the champion,
session bootstrap (10,000 resamples, seed 20261105), one-sided lower bound at alpha = 0.05 / 4 (Bonferroni).
PASS needs lower bound > 0 AND a 1st-percentile per-trade net no worse than the champion's minus 5 points.
The champion computed here must equal `alert_fade_test.score` exactly (guard; nothing is reported otherwise).

## Held-out and forward
The held-out period 2026-07..10 was already used once for the champion, so it is reported as SECONDARY only and
cannot promote anything. Every challenger runs in shadow nightly on forward events next to the entry/hold
challengers. At the forward decision point (n >= 300, amendment F1) a dev-PASS exit is RECOMMENDED only if its
forward paired daily difference has a session-bootstrap lower bound > 0 at alpha = 0.05 / (number of dev-PASS
exits). Changing the live exit needs the owner's OK and a prereg amendment; never automatic.

## Secondary (reported only)
Mean / win / 1st percentile per challenger; median minutes held; share of trades exited by the signal, the stop
and the cap; per-challenger held-out (secondary).
Trial ledger run id `fade-exits-2026-11-05-dev`.
