# Pre-registration: the trader's trend matrix as he uses it, a position rule on a stock in play (2026-11-18)

Status: FROZEN before the script exists and before any number below is computed. Script
`scripts/trend_book_study.py`, run ONCE on training. Report only.

## Why
`prereg_trend_matrix_exit_2026-11-18` tested the trader's matrix as an exit bolted onto the long book's own entry,
and read "MACD going red" as "MACD is red". The owner, the same evening: "take the trader's advice to heart and
don't discount it because your tests said so, this is real life advice on stocks straight from experience."
Read again, his words describe something else: "As long as the 1M shows all green, stay in the trade. No need to
exit ... MACD going red is usually a sign to start exiting." That is a rule for a stock he is watching all
session: in while the 1-minute column is all green, out when MACD turns red, in again when the column is green
again. On SKYA that day (official candles, this code): MACD green from 13:40, red at 13:54 (out about +1.5%, where
our timer also sold), and the column all green again for the afternoon leg that ended above 3.80. The earlier
test could not see that second leg: it allowed one entry, ours.

## Events and candles
The long book's training events (before 2026-07-01; fades and halts, as `long_feed_replay_study.events`), official
1-minute candles; the six conditions with the trader's settings as in `trend_matrix_exit_study.matrix`, computed
over the day's candles from the first pre-market candle. The stock is "in play" from the alert's start bar to the
session's close.

## The rule (TREND)
At each 1-minute close from the start bar on:
- flat and the 1-minute column all six green -> buy the next candle's open (no new entry from the book's last
  entry minute on);
- in a position and MACD red -> sell the next candle's open;
- still in at the session's last candle -> sell its close.
Any number of round trips per stock and day. No stop and no target: the trader named none. Each round trip pays
the event's modelled round-trip cost.
Variant TREND2: an entry also needs the 2-minute column all green (2-minute candles completed so far).

## Decided (training; run once)
TREND and TREND2 each PASS when the lower bound of the mean net per ROUND TRIP is above 0 (session bootstrap, 10,000
resamples, seed 20261118, one-sided alpha = 0.05 / 2; no bound = fail).

## Reported, not decided (so a failed gate still says what the method does)
Round trips per stock-day; mean BEFORE costs and after; win rate; minutes held; the result per stock-day (all its
round trips added); the best and worst 1% of round trips; first round trip against later ones; by time of day and
by price band; what the same rule earns at a cost of 0.2% a round trip (a careful trader working limit orders in
a liquid name) next to the modelled cost.

## What follows
- A PASS: scored once on the held-out events; a pass there goes to the owner as a paper book of its own.
- No pass: recorded together with the gross numbers. Independently of the gate the matrix goes into the live
  texts and trade cards and the trader's rule is tracked on every live long (owner's instruction): the live record
  is his method on real days.

## Known limits
A chart carries earlier days into its averages; here they start with the day's first pre-market candle. The
trader acts by eye on a live chart and picks his stocks; this is every alerted stock, mechanically, at the next
candle's open. His 3 and 5 minute columns are not used.
