# Pre-registration: do our own alert signals on the alphas carry an edge? Ten years of minute candles (2026-11-18)

Status: FROZEN before the script exists and before any number below is computed. Script
`scripts/scanner_signals_study.py`, run ONCE on training. Report only.

## Why
Owner, 2026-11-18: "it's time to work on the scanner for our alphas and for other tickers so another source of
input signals in addition to the meta traders alerts", and of three ways to start chose: test the signal list on the
alphas' history first, build live only what passes.
Why the alphas can carry a scanner at all: we hold official 1-minute candles for all 21 from 2016; on big stocks
the free live feed draws nearly the real candle (NVDA, MSFT, WMT on 2026-11-18: every minute present, highs and
lows within 0.05% on 94 to 99% of minutes); and a round trip costs about 0.04% against 0.7 to 0.9% on the channel's
small caps. What speaks against: every earlier attempt at fast trading on these stocks failed (30-minute
per-stock models, the learned fast books of `prereg_fast_megacap_2026-11-03` on held-out 2024-2026, intraday
momentum, the opening-range breakout). Those were learned forecasts on 30-minute bars or fixed-time rules. Discrete
1-minute events with volume, the kind an alert channel posts, were never tested on these stocks.

## Data and split
Official 1-minute regular-hours candles (`data/alpaca`). Gate set: the 20 liquid alphas AAPL AMZN GOOGL META MSFT
NVDA TSLA AMD AVGO COST JPM LLY MA NFLX ORCL UNH V WMT XOM RDDT. AAOI is scored and reported apart (thinner, a
different cost). Training: sessions 2016-01-04 to 2024-12-31. Held-out: 2025-01-02 to 2026-10-30, opened once and
only for what passes. Note: 2024 to 2026 was scored once before for a different family (learned fast books).

## Signals (each from closed candles only; s = the stock's own noise: the standard deviation of its 1-minute
returns over the previous 30 candles of the day, at least 15; v20 = mean volume of the previous 20 candles, at
least 10)
1. VOLUME SPIKE: candle volume >= 5 x v20 and |candle return| >= 2 x s. Direction: the candle's.
2. NEW EXTREME: at least 30 candles into the day, the close is above every earlier high of the day (below every
   earlier low) on volume >= 2 x v20. Direction: up for a new high, down for a new low.
3. BURST: |return over the last 5 candles| >= 4 x s x sqrt(5). Direction: the move's.
4. CROSS: at least 45 candles into the day, the EMA 9 of the day's closes crosses above (below) the EMA 21 on a
   candle with volume >= 3 x v20. Direction: up for a cross above.
Eight signals (four kinds, up and down). Only candles starting 09:45 to 15:25 ET can signal. Per stock and day,
only the FIRST signal of each kind and direction counts.

## The trade that scores a signal
Enter at the next candle's open in the signal's direction (long after an up signal, short after a down signal),
leave at the close of the candle 15 minutes and 60 minutes after the entry (the session's last candle if earlier),
and at the session's close. Cost: 0.04% per round trip (0.10% for RDDT), the daily model's figure; also shown at
twice that. No stop.

## Decided (training; run once)
Twenty-four cells (8 signals x 3 exits), each tested both ways, 48 one-sided tests at alpha = 0.05 / 48, bootstrap
by SESSION DATE across all stocks (10,000 resamples, seed 20261118; a cell under 300 signals or without a bound
fails):
- FOLLOW passes when the lower bound of the mean net of following the signal is above 0;
- FADE passes when the lower bound of the mean net of trading against it is above 0.

## Reported, not decided
Per cell: signals, signals per day across the 20 stocks, mean gross and net, win rate, the same at twice the cost;
per year; per stock; by time of day (09:45-10:30, 10:30-14:00, 14:00-15:25); AAOI apart at a 0.30% round trip.

## What follows
- A pass: that cell alone is scored once on the held-out sessions (alpha = 0.05 / number of passes). A pass there
  goes to the owner as the first live scanner signal: texted, recorded and paper-traded with this entry and exit.
- No pass: recorded. The scanner is then not built on these signals; the next candidates (a wider list of liquid
  stocks, other signal kinds) need their own frozen plan.

## Known limits
A signal's trade enters at the next candle's open: a live scanner on polled candles is a few seconds later (the
alpha minutes move about 0.1 to 0.2%, so seconds matter less than on small caps, but they are not modelled). The
cost is a flat figure, not each minute's spread. Twenty stocks picked knowing they became giants: they rise on an
average day, which favours every long cell slightly (about 0.001% per minute held).

## Result (2026-11-18, training run once; `reports/scanner_signals/result_train.json`)
Plan `e6fbb6c`, code `d061a90`. 20 stocks, 2,264 sessions (2016-01-04 to 2024-12-31), 68,474 first-of-the-day
signals: about 30 a day across the 20 stocks.

**No cell passes, in either direction.** The held-out sessions stay closed.

| signal | a day | move after it, before costs: 15 min / 60 min / to the close | best net (follow) | best net (fade) |
|---|---|---|---|---|
| volume spike up | 3.3 | +0.016% / +0.027% / +0.026% | -0.014% | -0.057% |
| volume spike down | 3.3 | +0.003% / -0.000% / -0.002% | -0.038% | -0.038% |
| new high of day | 7.8 | +0.012% / +0.023% / +0.036% | -0.004% | -0.052% |
| new low of day | 7.5 | -0.001% / +0.007% / +0.005% | -0.033% | -0.039% |
| burst up | 0.9 | -0.009% / +0.004% / -0.018% | -0.037% | -0.022% |
| burst down | 1.2 | +0.001% / +0.032% / +0.051% | +0.011% (lower bound -0.088%) | -0.041% |
| 9/21 cross up on volume | 3.1 | +0.010% / +0.008% / +0.007% | -0.031% | -0.047% |
| 9/21 cross down on volume | 3.0 | +0.010% / +0.002% / -0.005% | -0.030% | -0.036% |

(For a down signal the move is that of the short: positive = the stock kept falling.)

Reading:
1. After every one of the eight signals the stock moves, on average, between -0.02% and +0.05% over the next
   15 minutes to the close: nothing, against a cost of 0.04% (0.08% at twice the cost: every cell negative both
   ways). Win rates 44% to 52%.
2. The small positive numbers after UP signals to the close (+0.03%) are these stocks' ordinary drift (they rose
   on an average day through these years), not information in the signal; the down signals show no mirror image.
3. The one cell above zero after costs (short after a down burst, held to the close: +0.011%) has a lower bound
   of -0.088% and is one of 48 tests.
4. AAOI apart (0.30% round trip): every cell -0.2% to -0.4% after costs.
5. This is the fourth family of fast signals to find nothing on these stocks (30-minute models, learned fast
   books, opening range and intraday momentum, now channel-style events). On big liquid stocks a price-and-volume
   event on a 1-minute chart tells nothing about the next hour.

Verdict: recorded. The scanner is not built on these signals. A scanner needs a different kind of input than
price and volume events on liquid stocks; candidates need their own frozen plan.
