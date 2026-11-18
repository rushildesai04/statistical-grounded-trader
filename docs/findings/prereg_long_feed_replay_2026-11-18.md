# Pre-registration: what the free feed's candles cost the long book, and does a pad under the stop repair it? (2026-11-18)

Status: FROZEN before the script exists, before any free-feed bar of a past alert is fetched and before any number
below is computed. Script `scripts/long_feed_replay_study.py`, run ONCE on training. Report only.

## Why
Owner, 2026-11-18: "find gaps in the implementation"; then: "work on the method we have in front of us with the
alpaca paper trading" (no new data source for now). The root-cause audit of that morning found that the live long
book does not trade the tested rule: it finds its setup and its stop on the free feed's candles (one exchange; 0.2%
to 2% of the volume of the three stocks watched that day), while every test of the rule used the official tape.
On the 18 live longs so far the live stop was tighter than the real candle's low in 12 (median 0.5%), and on the
14 scored both ways the live book made -3.54% per trade against -1.47% for the rule on real candles. CLRO that day:
free-feed low 7.16, real low 7.13; stopped to the cent at 7.16 (-1.7%) where the rule on real candles hit its target
(+2.0%). Fourteen trades prove nothing. The same comparison can be made on every tested alert, because past
free-feed bars are available: replay what the live book would have done on them.

## Events (training)
The long book's tested events before 2026-07-01: the fade events of `reports/fade_news` joined to
`reports/telegram/trades_pooled_first.csv` (2,345) and the training halt events of
`reports/fade_halt_audit/halt_events.csv` (121). Official 1-minute regular-hours bars (`data/alpaca`); free-feed
1-minute bars of the same sessions fetched once and cached (`data/iex_1m`).

## The book's rule (as live today), the same for every leg
Long setups after the alert (first red candle both ways, bull flag v2, break of structure; at a halt reopen the
first red candle both ways); each simulated by `patterns.simulate` (stop first, target at 2x the risk, 30 minutes,
last entry minute); the first to fill whose 2x-risk target is larger than the event's modelled round-trip cost
(ties by the book's kind order). Scored with the red-fill-candle exit. Size w = min(1, 0.02679 / stop distance).
Per event the leg's CONTRIBUTION is w x (gross - cost), 0 when it does not trade.

## Legs
- TAPE: setups found and simulated on official bars from the research start bar (the tested rule). Without the
  cost check it must reproduce the stored test (`reports/long_risk/base.csv`, training rows) or nothing is reported.
- LIVE: setups found by `long_shadow.setups` on the FREE FEED's bars from the alert's posting time, exactly as the
  live book does (start bar, the $1-50 check and "halt" all judged on those bars), each simulated on those bars;
  the first to fill that can win is the live order (kind, fill bar, trigger, stop). Its result is that order
  replayed on official bars from its fill bar (`long_book._score`'s method). No fill there = void = 0.
- PAD p, p in {0.5%, 1.0%, 2.0%}: LIVE's order with its stop lowered by p of the stop price, before the size, the
  target and the replay. The order picked is LIVE's, unchanged.

## Decided (training; run once)
A. The handicap: mean over events of (LIVE - TAPE) contribution, with its 95% session-bootstrap interval. A real
   handicap = the upper end below 0.
B. The repair: PAD p PASSES when the mean over events of (PAD p - LIVE) contribution has a one-sided lower bound
   above 0 at alpha = 0.05 / 3 (10,000 resamples, seed 20261118; no bound = fail).

## Reported, not decided
Per leg: trades, mean net per trade, win rate, share stopped on the fill bar, voids. LIVE against TAPE on the same
event: same kind, same fill minute, the live stop against the tape stop (share tighter, median gap), events the
free feed calls a halt that the tape does not. The free feed's share of the session's volume. TAPE with the same
three pads (is a lower stop good for the rule itself, or only a repair of the feed?). PAD by the free feed's volume
share (thin half against thick half).

## What follows
- B PASS: the passing pad with the highest lower bound is scored once on the held-out events (2026-07-01 on; their
  free-feed bars are fetched only then). A pass there is put to the owner as a change of the live stop; nothing
  changes before.
- B FAIL: recorded; a pad is not tried again. The handicap, if real, then stays a data problem: the book needs
  real candles, and until it has them its live record is the rule's record on other candles.

## Known limits
Past free-feed bars as served today stand in for what the live book saw at the time (on the 2026-11-18 rows they
show the same opens and lows as the live rows). Both legs use the event's modelled cost. The live book's 30-second
polling and its text delay are not modelled: fills are at the bar prices, as the book books them.
