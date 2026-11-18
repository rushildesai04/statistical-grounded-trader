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
