# Pre-registration: the trader's trend matrix as the long book's exit (2026-11-18)

Status: FROZEN before the script exists and before any number below is computed. Script
`scripts/trend_matrix_exit_study.py`, run ONCE on training. Report only.

## Why
SKYA, 2026-11-18: the long book bought 3.33 and its 30-minute timer sold 3.40; the stock closed 4.13 after a high
of 4.39. The trader, through the owner: "it left a lot of $$ on the table ... This is called a trend matrix
[six indicators on the 1, 2, 3 and 5 minute charts]. As long as the 1M shows all green, stay in the trade. No need
to exit. These are listed in order of importance. MACD going red is usually a sign to start exiting the trade."
He sent the indicator's settings. Tested before and lost: holding a winner past its target while MACD (12/26/9)
is positive, a swing-low trail after the target, the trader's five entry rules. Not tested: his own exit as he
states it, from the entry on, in place of the book's target and timer.

## The matrix (1-minute chart; his settings; our own code from the standard definitions)
Six conditions at each 1-minute close, computed over the day's official candles, pre-market included:
1. MACD: (EMA 12 - EMA 31) above its EMA 10.
2. EMA: close above EMA 34.
3. RSI: RSI 14 above 50.
4. Bollinger: close above the 20-candle average (the band's middle).
5. Supertrend: up (ATR as the 10-candle average of the true range, multiplier 3).
6. PVT: price-volume trend above its EMA 15.

## Trades
Entries: the long book's own, unchanged (the first setup to fill whose target beats the cost, as live), on the
book's tested events with official candles: training before 2026-07-01, held-out from then.
Three exits for the same entry, equal size, the event's modelled cost:
- NOW: the book's exits (stop, target at twice the risk, red entry candle, 30 minutes).
- MACD: the book's stop stays; no target and no timer; sell at the next candle's open after the first 1-minute
  close, from the entry candle on, at which MACD is red; else the session's close.
- MATRIX: the same, selling after the first close at which ANY of the six is red (the 1M column no longer all
  green).
A stop is filled at the stop price (at the candle's open when it opens below).

## Decided (training; run once)
MACD and MATRIX each PASS when the lower bound of the mean net PER TRADE is above 0 (session bootstrap, 10,000
resamples, seed 20261118, one-sided alpha = 0.05 / 2; no bound = fail). Losing less than NOW is reported and is not
a pass.

## Reported, not decided
Per exit: trades, mean net, win rate, median minutes held, share stopped, share sold on the first candle; the
paired difference to NOW per trade; the best 1% of trades (how much of the mean a few SKYAs carry); by setup; trades
whose entry had fewer than 60 candles of history that day (the indicators are not settled there) apart; the result
of entering only when the 1M column is all green at the entry.

## What follows
- A PASS: that exit is scored once on the held-out events; a pass there goes to the owner as the long book's exit.
- No pass: recorded; the matrix stays what the chart read in the texts already is, context.

## Known limits
A chart carries earlier days into its averages; here they start with the day's first pre-market candle, so early
trades see less settled indicators (reported apart). The 2, 3 and 5 minute columns are not used: the trader's
rule is on the 1-minute column.

## Result (2026-11-18, training run once; `reports/trend_matrix_exit/result_train.json`)
Plan `aa66749`, code as committed before the run. 2,139 trades of the long book (the tested entries), 353 sessions.

**Neither exit passes.** The held-out events stay closed.

| exit | mean net per trade | wins | median time in the trade | stopped | against the book's exits, per trade |
|---|---|---|---|---|---|
| NOW (stop, 2x target, red candle, 30 min) | -2.07% | 16% | | | |
| MACD red | -2.31% (lower bound -2.64%) | 14% | 1 minute | 67% | -0.24 points [-0.52, +0.04] |
| any of the six red (1M column) | -2.20% (lower bound -2.53%) | 14% | 1 minute | 63% | -0.13 points [-0.41, +0.17] |

By setup the picture is the same (break of structure, SKYA's setup: -1.52% now, -1.83% with the MACD exit, -1.56%
with the matrix; wins up from 20% to 27-28% and smaller). Trades whose indicators had at least 60 candles behind
them: -2.03% now, -2.21% and -2.16%.

Reading:
1. The rule cannot do its work from the book's entries: the median trade lasts ONE minute under either exit.
   Two trades in three are stopped before any trend exists to stay in, and most of the rest see a red condition
   at once. No trade was held to the close.
2. Entering only when the 1M column is all green makes it worse, not better: those 1,674 entries lose -2.15% with
   the book's exits (-2.46% / -2.34% with his), the 465 others -1.77%. "Everything green" marks a stock that has
   already run.
3. SKYA itself, by this code on the day's official candles: at the candle before the entry (13:38) MACD and PVT
   were RED, so the rule sells at 13:40 for +0.3%, less than the timer's +1.5%; the column went all green later,
   inside the run. The matrix describes a trend once it is under way. It did not say, at 13:39, that this was the
   one in six with legs, and that is the whole problem: by the time the column is green and stays green, the
   information is the move itself.
4. The earlier results stand with this one: holding a winner while MACD is positive, a swing-low trail, the
   trader's five entry rules, now his own exit from the entry on. On this channel's alerts a way to stay in the
   rare runner costs more on the many that turn than it earns on the few.

Verdict: recorded; the long book's exits are unchanged. The matrix can be shown as context on a trade card (it is
a fair picture of "still trending or not"), never as the exit.

Limits met in the run: indicators start with the day's first pre-market candle (a chart carries earlier days);
the trader's 2, 3 and 5 minute columns were not used; a discretionary reader who enters only once the trend is
visible is a different trade than the book's and is not what was tested.
