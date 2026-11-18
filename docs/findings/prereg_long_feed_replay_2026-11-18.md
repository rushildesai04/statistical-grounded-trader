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

## Result (2026-11-18, training run once; `reports/long_feed_replay/result_train.json`)
Plan `2bdc2db`, code `01dd252`. 2,464 events, 354 sessions; free-feed bars for all 2,372 stock-days (14 had none).
TAPE reproduces the stored test exactly (2,242 stored trades, 0 mismatched, 0 extra). The free feed carried a median
0.55% of a session's volume (10th to 90th percentile: 0.25% to 1.74%).

| leg | trades | mean net per trade | win | contribution per event | stopped on the fill bar |
|---|---|---|---|---|---|
| TAPE (the tested rule, real candles) | 2,139 | -2.07% | 16% | -1.19% | 45% |
| LIVE (found on the free feed, scored on the tape) | 1,463 (+106 void) | -1.34% | 21% | -0.58% | 35% |
| LIVE, stop 0.5% lower | 1,529 | -1.12% | 25% | -0.47% | |
| LIVE, stop 1.0% lower | 1,549 | -1.09% | 26% | -0.42% | |
| LIVE, stop 2.0% lower | 1,559 | -1.12% | 25% | -0.38% | |

**A. The handicap is NOT real: it has the other sign.** LIVE - TAPE per event: +0.61% [+0.51, +0.70]. The book on
the free feed's candles loses LESS than the tested rule on real candles, per event and per trade (-1.34% against
-2.07%; on the 1,380 events both trade: -1.31% against -1.60%). The claim of that morning (the thin feed makes the
live book worse; 14 trades, -3.54% against -1.47%) was a small-sample reading and is withdrawn. What is true:
the two are different books. Same setup in 807 of the 1,474 events both trade, same setup and fill minute in 585;
the live stop is tighter in 77% (median 0.58%); the feed sees a halt 79 times where there was none; and 665 events
the rule trades on the tape produce no trade on the feed. Those 665 are the rule's worst trades (-2.95% per
trade, win 9%, 55% stopped on the fill bar): a stock with too few prints on the small exchange to draw a setup is
a stock the long rule should not touch. The thin feed has been acting as a crude liquidity filter.

**B. All three pads PASS** (PAD - LIVE per event: +0.11% [lower bound +0.07], +0.16% [+0.10], +0.20% [+0.12]).
Read with one warning about the gate I froze: contribution is size x net and the size shrinks as the stop
widens, so on a book that loses, a wider stop "improves" contribution partly by trading smaller. Per trade, on the
same 1,463 trades: -1.34% becomes -1.11% (0.5%), -1.10% (1.0%), -1.16% (2.0%); win rate 21% to 25%. So about
0.2 points per trade is real and it is all there at 0.5%; the rest of the 2.0% pad's score is smaller size. The
same pads on the real-candle rule: +0.15, +0.08 and -0.03 points per trade. A lower stop repairs the feed's
too-tight low; it is not a better rule.

Per the plan the pad with the highest lower bound (2.0%) is scored once on the held-out events; the report there
gives the per-trade result of all three, and the proposal to the owner will rest on the per-trade numbers.

## Leads seen in the event file (exploratory; each needs its own frozen plan before it is a result)
- 45% of the tested rule's trades are stopped on the bar they fill; for the setup the live book takes most
  ("red-candle close": buy the open after the first red candle, stop at its low; 1,045 trades) it is 68%, with a
  13% win rate. With a target of twice the risk a trade needs to win more than a third of the time. The setup's
  geometry, not its direction, kills it: a 1.8% stop (median) inside minutes that swing 6% to 9%.
- By stop distance: the tightest fifth (under 1.35%) is stopped on the fill bar 77% of the time, the widest
  fifth 9%; yet the net result gets WORSE as the stop widens (-1.35% to -2.50%), because a wider stop loses
  more each time and the win rate barely moves (13% to 19%). Neither a tight nor a wide stop saves this entry.
- Break of structure (441 trades): 2% stopped on the fill bar, 20% wins, -1.52%. Bull flag v2: 27 trades.

## Held-out result (2026-11-18, run once; `reports/long_feed_replay/result_holdout.json`)
822 events from 2026-07-01 on, 87 sessions; free-feed bars for all 774 stock-days (2 had none; median share of
volume 0.57%). TAPE reproduces the stored held-out test exactly (724 trades, 0 mismatched, 0 extra).

| leg | trades | mean net per trade | win | contribution per event |
|---|---|---|---|---|
| TAPE | 689 | -2.32% | 14% | -1.15% |
| LIVE | 487 | -1.84% | 18% | -0.65% |
| LIVE, stop 0.5% lower | 508 | -1.71% | 20% | -0.60% |
| LIVE, stop 1.0% lower | 511 | -1.72% | 20% | -0.57% |
| LIVE, stop 2.0% lower | 519 | -1.60% | 21% | -0.45% |

- **A again: no handicap.** LIVE - TAPE per event +0.50% [+0.33, +0.67]. Same picture as training: same setup and
  fill minute in 186 of 481 events both trade, live stop tighter in 73% (median 0.68%), 208 events only the tape
  trades.
- **B, the registered test (pad 2.0%): PASS by the frozen gate** (PAD - LIVE per event +0.20%, lower bound +0.07%).
  The other two would not have passed (+0.05% and +0.08%, lower bounds below 0).
- **Per trade, on the same 487 trades (the number the proposal rests on):** -1.84% becomes -1.76% (0.5%),
  -1.77% (1.0%), -1.69% (2.0%); differences +0.07 [-0.09, +0.24], +0.07 [-0.13, +0.28] and +0.15 [-0.14, +0.45]
  points; win rate 18% to 19-20%. None is distinguishable from zero. The 2.0% pad passes the gate mostly because
  a wider stop makes the book trade smaller, and a smaller losing book loses less.

Put to the owner as written in the plan, with this recommendation: do not present the pad as a repair. It is
worth about 0.1 point per trade at best, on a book that loses 1.7 to 1.8% per trade; the same money is saved,
honestly labelled, by a smaller size. The stop stays where it is unless the owner wants the pad.

Correction to the record: both runs of this study drew their bootstrap through the shared helper of
`trader_rules_long_study` (seed 20261112, 10,000 resamples), not the seed 20261118 written in the plan. The
intervals above are from that seed; nothing was re-run.
