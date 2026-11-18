# Pre-registration: the 9/21 cross bought the instant volume is already there, on official prints (2026-11-18)

Status: FROZEN before the script exists, before any print of these minutes is fetched for this question and before
any number below is computed. Script `scripts/ema_instant_volume_study.py`, run ONCE on training. Report only.

## Why
Owner, 2026-11-18, after `prereg_ema_exact_cross_2026-11-18` failed: "this is not any time to ... defend our work.
You need to challenge it ... obviously we're waiting too long. The signals come in late for the actual buys."

The challenge to that study, taken seriously:
1. Its tradeable volume rule (the minute BEFORE the cross at 2x) is weak. The trader's claim is about volume that
   is rising AS the cross happens. The only row with a positive gross move was SPIKE (the cross minute ends at 3x
   or more): +0.29% in 30 minutes from the exact cross price, and there a minute of delay cost 0.31% and, on the
   crosses that held, the whole move (+0.93% against +0.04%). That row was set aside as look-ahead because the
   minute's final volume is not known at the cross. But part of it IS knowable: the crosses that happen when the
   minute has ALREADY traded 3x. That version was never run for the plain 9/21 cross (the 2026-10-19 print replay
   used 9/24 with a 5x spike and four more conditions).
2. It had no way out of a failed cross; a trader sells a cross that un-crosses.
3. Our own live long book is late: its texts went out 64 to 88 seconds after the booked entry price (live rows,
   2026-11-11 to 11-17), so nothing in the live record says what an on-time entry does.

## Events
The SPIKE events of the exact-cross training run (`reports/ema_exact_cross/train_events.csv`: gapper universe,
sessions before 2026-04-01; 9,686 minutes i with e9 <= e21 after minute i-1, high[i] >= P*, final volume >= 3x the
mean of the 10 minutes before). A trigger below can only happen inside such a minute, so no trigger is lost by
fetching only these. Official prints of each minute, cleaned as `sgt.decision.intrabar.clean_trades`.

## Trigger (knowable at its instant)
The first print of minute i at which BOTH hold: its price is above P* (the live EMA 9 is above the live EMA 21) and
the minute's cumulative printed volume up to and including it is at least 3x the 10-minute base. No such print: no
event.

## Entries, on every triggered event
- FAST: the first print at least 1 second after the trigger (within 60 seconds, else no trade): a market order
  sent at the trigger. This is the trader's "exactly at the cross" as it can be done.
- NEXT: open[i+1]; PLUS1: open[i+2] (the first regular bar at or after, as amendment 1 of the exact-cross plan).
Marks: the close of minute i+5, i+15, i+30, as there; the event sets per mark are that study's.

## Exits for FAST (two, fixed here)
- HOLD: to the mark.
- CUT: when the cross has failed by the close of minute i (close[i] <= P*), sell at open[i+1]; otherwise hold to
  the mark.
Cost: the measured gapper round trip of the event (as the exact-cross study).

## Gate (training; run once)
PASS if the mean net of FAST has a session-bootstrap one-sided lower bound above 0 at alpha = 0.05 / 6 in at least
one of the six cells (HOLD or CUT, at 5 / 15 / 30 minutes; 10,000 resamples, seed 20261118; no bound = fail).

## Reported, not decided
Triggered share of the SPIKE events and the trigger's second within the minute; gross and net of FAST, NEXT and
PLUS1 at each mark; the price of NEXT and PLUS1 against FAST (the cost of a minute, on knowable events); failed
crosses against held ones; FAST by price ($1-2, $2-5, $5-10, above $10; below $1 apart) and by time of day; the
gross of FAST against the event's modelled cost by cost quartile (is there a group cheap enough to trade?).

## What follows
- PASS: the passing cell is run once on the closed sessions (2026-04-01 on) before anything else. A pass there
  becomes a forward shadow on the live listener; nothing trades on it without the owner's OK.
- FAIL: recorded. The plain cross is then closed in all three forms (bar close, exact price, instant volume on
  prints); a reported breakdown that looks good is a lead for a new frozen plan, never a result.

## Known limits
A print is not a fill: FAST buys at a print one second after the trigger, in a minute that is trading 3x its
usual volume; the modelled cost charges the usual spread. The base volume comes from bars and the live volume
from cleaned prints (odd lots and off-tape prints excluded), so a few triggers come later than on a chart.
