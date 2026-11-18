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

## Result (2026-11-18, development run once; `reports/earnings_midcap/result_dev.json`)
Plan `fd31be9`, code `f6b0188`. Universe 1,118 symbols; 313 have no current SEC ticker mapping (delisted names and
funds), 695 have filings and bars, 683 have events in the window. 18,149 earnings events 2017-2023; 6,522 with a
move of 5% or more on the earnings day (about 78 a month). (The earlier study's ticker list, excluded here, has
99 names; its text says 98.)

**Development: FAIL.** Mean net per event -0.281% (lower bound -0.436%), 47% winners; before the 0.30% cost the
follow-through is +0.02%, nothing. At twice the cost -0.58%. The held-out years stay closed.

| view | events | mean net |
|---|---|---|
| long leg (after a rise) | 3,347 | -0.20% |
| short leg (after a fall) | 3,175 | -0.37% |
| move 5-10% / 10-20% / above 20% | 4,220 / 1,907 / 395 | -0.25% / -0.21% / -1.01% |
| exit after 1 / 3 / 5 sessions | 6,522 | -0.27% / -0.22% / -0.28% |
| least / middle / most traded third | 2,189 / 2,179 / 2,154 | -0.13% / -0.46% / -0.25% |
| 2017 to 2023, by year | | +0.06, -0.48, -0.24, -0.47, -0.43, +0.08, -0.47% |

Reading: on these mid-size stocks a 5% earnings-day move is followed by no further move in the same direction
over the next week; the biggest moves (above 20%) partly reverse. The large caps showed +0.41% in the same years
under the same rule and lost it afterwards; the mid-size stocks do not show it at all. Survivors-only events
would, if anything, flatter the long leg, and it is negative. The earnings follow-through is closed for mid-size
stocks too: it is not a scanner input.
