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

## Result (2026-11-18, training run once; `reports/trend_book/result_train.json`)
Plan `9514d67`, code as committed before the run. 2,464 alerted stock-days; the rule traded on 96% of them.

**Neither rule passes the gate.** The held-out events stay closed.

| | TREND (1M column) | TREND2 (1M and 2M) |
|---|---|---|
| round trips | 16,643 (7.1 a stock-day) | 11,368 (4.9) |
| time in a round trip (median) | 11 minutes | 10 minutes |
| per round trip BEFORE costs | -0.21% [-0.37, -0.03] | -0.34% [-0.57, -0.11] |
| modelled cost | 0.95% | 0.94% |
| per round trip after costs | -1.15% (lower bound -1.32%) | -1.28% (-1.51%) |
| winners | 24% | 24% |
| best 1% of round trips, average | +62% | +67% |
| worst 1%, average | -26% | -28% |

What the reported numbers say about the method itself:
1. **It is a rule for catching the rare big one, and it does catch it.** One round trip in a hundred makes +62% on
   average; one in four wins at all. Run on every alerted stock all session, the many small losses add up to a
   little more than the few big wins (-0.2% before costs), and then costs take 0.95% each time, seven times a day.
2. **The first entry after the alert is where it loses** (-1.58% before costs, 2,446 round trips); every later
   round trip together is flat before costs (+0.03%, 14,197). In the first hour -1.2%, after 13:00 +0.02%. The
   matrix turning green right at the alert is the top of the first spike more often than the start of a leg.
3. **It is better on dearer stocks**: above $10, +0.29% before costs (2,244 round trips; -0.38% after); $1 to $2,
   -0.36%.
4. At a careful trader's cost (0.2% a round trip) the whole set is still -0.41%.

What this run cannot say: whether the trader makes money with it. He does not take every alert, he does not
enter on the first green of the first spike, and he reads the tape. The run says the matrix alone, applied to
everything, is not the edge; the choice of stock and moment is. That is the part to learn from him: which alert
he would trade and which green he would wait out.

Verdict: recorded. As planned, independently of the gate: the matrix goes into the live texts and cards and the
rule is tracked on every live long, so real days show what it does where a person would use it.
