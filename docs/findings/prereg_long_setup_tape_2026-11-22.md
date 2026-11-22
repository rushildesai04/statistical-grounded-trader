# Plan: did each recorded long setup exist on the full tape? (retrospective diagnostic, 2026-11-22)

Status: FROZEN before any full-tape bar of these alerts is loaded by the script. Script
`scripts/long_setup_tape_check.py`; output `reports/long_setup_tape/`. Owner's brief of 2026-11-22: "Reconstruct every
recorded long setup on full-tape data ... Keep this retrospective diagnostic separate from the forward cohort."

It is a diagnostic of DATA VALIDITY. It computes no return, selects no parameter, changes no rule and writes nothing
to the nightly ledger or to any registered sample. The forward cohort (trades from 2026-11-23) is not read.

## What is already known (not repeated here)
- `prereg_long_feed_replay_2026-11-18`: on 2,464 training alerts the book run on the free feed's candles lost LESS
  than the tested rule on real candles (+0.61% per event, interval +0.51 to +0.70). The thin feed is not what makes
  the long rule lose in the test.
- `long_bot_replay_2026-11-19`: the feed shows a base narrower than the tape does; the base cap was halved for it.
This diagnostic asks a narrower question, per recorded trade: was the pattern the book acted on there on the tape?

## Part A: the recorded setups
Population: every row of `long_trades` that records a setup (a kind): 24 bought, 3 seen while the book was off, 2
missed. 29 rows, 2026-11-10 to 11-20. Their kinds: first red candle (L4a 13, L4b 9), break of structure (L6 2), halt
reopening (L7a 2), base break (L8 3). Only L8 is still live; 26 of the 29 are setups the book no longer trades.

Bars: regular-session 1-minute bars, unadjusted, of the free feed and of the official tape, fetched today.
Detectors: today's code, as the live book calls it (`long_shadow.setups` with the alert's posting time, plus
`patterns.base_break` from the same start bar); each setup filled by `patterns.simulate` (last entry 15:45 ET).

Step 1, control, on the free feed only (run and checked BEFORE the tape is loaded): the recorded setup is REPRODUCED
when a setup of the recorded kind fills in the recorded entry minute (for a base break bought at its candle's close:
its `ready` is the recorded entry time) with a stop within half a cent of the recorded stop. If the matching
mechanics need a correction it is made here, on the feed only, and stated.

Step 2, the tape, once. Each recorded setup gets exactly one class:

| Class | Condition |
|---|---|
| INSUFFICIENT EVIDENCE | not reproduced in step 1 (today's detector is then not the one that made the trade, or the feed's stored bars differ from the live ones), or no tape bars |
| EXISTED | on the tape a setup of the same kind completes on the same candle (same `ready`) and fills in the same minute |
| EXISTED, TIMING DIFFERED | on the tape a setup of the same kind fills, but on another candle or in another minute |
| THIN FEED ONLY | on the tape no setup of that kind fills for this alert (none found, or found and cancelled / never triggered) |

Reported next to the class, not part of it: the tape's stop against the recorded stop (how many differ by more than a
quarter of the recorded risk), the minutes between the two fills, and whether the tape calls the alert a halt where
the feed did (or the reverse).

## Part B: the rule that is live now, on the same alerts
The 29 rows above mostly describe retired setups. So the rule live from 2026-11-20 (base break L8 + bull flag L5v2,
`long_book.live_setups`) is run on both sets of bars for every first qualifying alert of 2026-11-10 to 11-20 (82: the
66 the book watched and the 16 it did not). Existence only: the cost checks and the rule about our own open short are
not applied, no trade is priced. The rule's pick is its earliest setup (a base break counts at its `ready`, a bull
flag at its fill). One class per alert:

| Class | Condition |
|---|---|
| BOTH NONE | no pick on either |
| SAME | same kind, same minute |
| DIFFERENT | a pick on both, another kind or minute |
| THIN FEED ONLY | a pick on the feed, none on the tape |
| TAPE ONLY | a pick on the tape, none on the feed |
| NO DATA | bars missing on either |

For SAME: how many tape stops differ from the feed's by more than a quarter of the feed's risk.

## What it can and cannot show
It can show how often the thing the book acts on is an artefact of a feed carrying under 1% of the volume. It cannot
show that real candles would make money (the 11-18 replay says the rule loses on them), and a class is not a cause of
a loss: the trade's result on the tape is the execution model's business, and is not joined to these classes here
except as a count of losers per class. 29 and 82 are small numbers; no percentage from them is a rate to rely on.

## Not done
No return by class beyond the count of losers, no new parameter, no re-run after the tape is loaded, no forward rows.

---
## Result (run once, 2026-11-22; plan `59c518f`; `reports/long_setup_tape/result.json`)
Step 1, control: 29 of 29 recorded setups reproduced on the free feed's stored bars with today's detectors (same
minute, same stop). No correction of the matching was needed; the only change before the tape was loaded was an
import path. Bars: 82 stock-days on each feed, none missing.

### A. The 29 recorded setups

| Class | Setups | of them bought | bought and lost |
|---|---|---|---|
| EXISTED (same candle, same minute) | 19 | 14 | 11 |
| EXISTED, TIMING DIFFERED | 7 | 7 | 5 |
| THIN FEED ONLY | 3 | 3 | 2 |
| INSUFFICIENT EVIDENCE | 0 | 0 | 0 |

- By kind: first red candle (L4a, L4b) 22: 17 existed, 4 differed in timing, 1 thin-feed only (RETO 11-12: the
  candle is on the tape, its order would have been cancelled). Break of structure (L6) 2: both on another candle
  (one 8 minutes earlier, one 1 minute later). Halt reopening (L7a) 2: 1 existed, 1 a minute later; the tape agrees
  with the feed on what was a halt in all 29. **Base break (L8, the rule live now) 3: 1 existed (LVWR), 2 thin-feed
  only (SVRE and JMIA, 11-20: on the tape those minutes were not a base under the same definition).**
- Timing, where it differed: the tape's fill was 8 minutes earlier to 1 minute later than the book's.
- The stop. Of the 26 setups that exist on the tape, the tape's stop is lower than the feed's in 19, equal in 5,
  higher in 2; in 14 of 26 it differs by more than a quarter of the trade's planned risk. A setup that "existed"
  therefore still had, more often than not, a materially different stop than the one the book used.
- Losers are not concentrated in the artefacts: 16 of the 18 losing longs were on setups that exist on the tape.

### B. The rule live now (base break + bull flag), all 82 first qualifying alerts, existence only

| Class | Alerts |
|---|---|
| BOTH NONE | 60 |
| SAME (kind and minute) | 4 |
| DIFFERENT (a pick on both, another minute) | 7 |
| THIN FEED ONLY | 7 |
| TAPE ONLY | 4 |
| NO DATA | 0 |

Of the 18 alerts where the feed shows the live rule a setup, the tape shows the same one in 4, another one in 7
(3 to 18 minutes apart, either way) and none in 7. Of the 15 where the tape shows one, the feed misses 4.

### What this does and does not establish
- Established: the base break the book acts on is, most of the time, not the tape's base break. On these 82 alerts
  the two agree on 4 of the 22 where either sees one. The one-candle setups the book used before were mostly real
  (17 of 22), but with a stop the tape puts lower.
- One caution on "thin feed only": the 4% cap on the base's width was set on 2026-11-19 for the FEED's narrower
  view. On the tape the same number is a stricter test, so some of the 7 are bases the tape shows wider, not minutes
  with no pattern at all.
- Not established: that real candles would help. The forward long sample measures "a pattern found on the free feed,
  executed on the real market", which is what the bot replay of 2026-11-19 tested (a loser there too). The replay of
  2026-11-18 found the book on feed candles losing LESS than the rule on real ones. Setup validity is a statement
  about what the record describes, not a route to profit.
- 3, 22 and 82 are small numbers. The direction is clear; no percentage here is a rate to rely on.

Consequence, as registered: none for any rule. The forward long sample keeps its definition and must be described
as above wherever its result is reported. A setup pattern on the tape can only be traded with real-time full-tape
data, which the book does not have (still the unmet data requirement).
