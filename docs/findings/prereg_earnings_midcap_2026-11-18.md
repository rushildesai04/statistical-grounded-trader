# Pre-registration: the earnings follow-through on MID-SIZE stocks (2026-11-18)

Status: FROZEN before any earnings date of these stocks is fetched and before any return around one is computed.
Script `scripts/earnings_midcap_study.py`. Report only.

## Why
Owner, 2026-11-18, choosing the next input for a scanner after price-and-volume events failed on the alphas:
"1" (earnings reactions). Correction made the same hour: that idea was already tested on 98 LARGE caps
(`prereg_earnings_2026-11-03`) and failed on its held-out years: a stock that moved 5% or more on its earnings day
kept going for five sessions by +0.41% in 2016-2023 and by +0.13% (lower bound -0.23%) in 2024-2026; "earnings-
reaction trading on large caps is closed". The published picture is that this drift has faded in the largest, most
watched stocks and lasts longer in smaller ones. Mid-size stocks were never tested here. This plan takes the ONE
configuration that study selected and applies it, unchanged, to a different universe. Nothing is chosen on the
new data.

## Universe (a rule, fixed here; computed from trading activity in 2016 only)
Symbols in `data/daily` with at least 200 sessions in 2016, a median daily dollar volume in 2016 from $10 million
to under $100 million, a median close of at least $5 in 2016, and not one of the 98 large caps of the earlier
study: 1,118 symbols (counted before this file was written; 981 of them still traded in October 2026). Kept for
the test: those with a current SEC ticker-to-company mapping and adjusted daily bars. Funds drop out by themselves
(no earnings filings).

## Events and trade (the earlier study's code, unchanged: `sgt.catalysts.earnings`)
- Event: an 8-K with Item 2.02, EDGAR acceptance time T. Reaction session R: T before 09:30 ET on a session ->
  that session; T at or after 16:00 ET or on a non-session day -> the next session; T during the session ->
  dropped. A repeat within 20 sessions of a kept event is dropped. Needs adjusted daily bars R-1 to R+5.
- Trade (`close_cont_m5%_x5`): when the stock's close on R is 5% or more away from its close on R-1, enter at the
  close of R in the direction of that move (long after a rise, short after a fall), leave at the close five
  sessions later. Result per event: side x (stock return - SPY return over the same days) - cost.
- Cost: 0.30% per event (a mid-size stock's round trip and the hedge), three times the large-cap figure; also
  shown at 0.60%.

## Development (events with R from 2017-01-03 and the exit on or before 2023-12-29)
The universe is screened on 2016, so 2016 itself is left out. PASS: at least 300 events and the one-sided 95%
lower bound of the mean net per event above 0 (bootstrap by reaction DATE, 10,000 resamples, seed 20261118; no
bound = fail). FAIL: the held-out years stay closed.

## Held-out (R from 2024-01-02, exit on or before 2026-10-29; scored once, only after a development PASS)
PASS only if all hold: lower bound above 0; mean above 0 at 0.60% cost; at least 2 of the calendar years 2024,
2025, 2026 with a positive mean; at least 100 events.

## Reported, not decided
Long leg and short leg apart; unhedged; exits after 1 and 3 sessions; by year; by size of the reaction (5-10%,
10-20%, above 20%); by 2016 dollar-volume tercile; events per month (how often a scanner would fire).

## What follows
- Held-out PASS: put to the owner as the scanner's first signal source: each evening the stocks that reported
  and moved 5% or more, entered at that close on paper, five sessions, with these rules; live record before money.
- Any FAIL: recorded; the earnings follow-through is closed for mid-size stocks too.

## Known limits
Survivors only on the event side: a company without a current SEC ticker mapping (acquired, bankrupt, renamed)
has no events here. The worst continuations after a fall (to zero) and the best after a rise (to a takeover) are
both missing; the long-minus-short design and the SPY hedge keep general market and survivor drift out of the
result, not this. Shorting needs borrow: the short leg is reported apart so the long leg can be judged alone.
Entry at the close of R assumes the move is known a few minutes before the close, as the daily models decide.
