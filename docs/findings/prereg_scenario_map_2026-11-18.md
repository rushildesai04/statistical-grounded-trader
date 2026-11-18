# Pre-registration: where in the move does the alert arrive, and a long entry that waits for the low to hold (2026-11-18)

Status: FROZEN before the script exists and before any number below is computed. Script
`scripts/scenario_map_study.py`, run ONCE on training (events before 2026-07-01). Report only.

## Why
Owner, 2026-11-18: "was it because the alerts came in too late?"; then: "fix the rule and come up with a creative
solution ... there's not just going to be one general rule that fits every scenario ... survey the rules and add
complexity and logic on top so we can account for many different scenarios."

Two facts from that day:
- The three longs watched that morning met three different situations. CUE: the alert was posted after the spike's
  top, the stock was already 8% off its high and fell 9% more before running. SVRE: the alert came at the high of a
  23% run and the stock was 24% lower within half an hour. CLRO: 34% into its run, at the high. One long rule was
  applied to all three.
- `prereg_long_feed_replay_2026-11-18`: 45% of the tested long rule's trades are stopped on the bar they fill; for
  its commonest setup (buy the open after the first red candle, stop at its low; 1,045 trades) 68% are, with 13%
  winners. Tight stops are hit by noise and wide stops lose more: the entry is the problem.

Never tested: the state of the move AT the alert. Everything tested so far looked at what happens after it
(time since the alert, price against the alert price, candles after it).

## Events and prices
The 2,345 training fade events (`reports/fade_news/dev_events.csv`, with the short's tested net and the news flags)
and their official 1-minute bars, pre-market included (`data/alpaca`). t0 = the research start bar (the first bar
at or after alert + 10 s; the short's entry bar).

## Part A: the scenario map
At t0, from bars BEFORE t0 only (pre-market from 04:00 ET included):
- RUN = open[t0] / (lowest low of the day so far) - 1: how far the stock has already come.
- OFF = open[t0] / (highest high of the day so far) - 1: 0 at the high, negative once it has turned.
Scenarios (six): RUN in three bands (cut at the training terciles of RUN, computed once on these events) x
AT THE HIGH (OFF >= -2%) or OFF THE HIGH (OFF < -2%). Events with fewer than 5 bars before t0 are left out
(counted).
Two outcomes per event, each for the whole event set of a scenario:
- LONG: buy open[t0], sell the close 30 minutes later (the bar starting 29 minutes after t0, or the session's last
  bar), no stop, minus the event's modelled round-trip cost.
- SHORT: the tested fade's net for that event (the stored result: 20% stop, 60 minutes, costs and locate).
Decided (training): twelve one-sided tests at alpha = 0.05 / 12 (session bootstrap, 10,000 resamples, seed
20261118; a cell under 40 events or without a bound fails): a scenario is a LONG scenario when the lower bound of
its mean LONG net is above 0; a SHORT scenario when the lower bound of (its mean SHORT net minus the mean SHORT net
of all other events) is above 0.

## Part B: an entry that waits for the low to hold (HOLD)
The rule it replaces buys the very next open after the first red candle R and is stopped in that minute two times
in three. HOLD waits: after R (the first red candle at or after t0, within 30 minutes, as today), the next k
candles must each keep their low ABOVE R's low. If one does not, there is no trade (today's rule would have been
stopped). If all k hold, buy the open of the candle after them; stop at R's low; target at twice the risk; out
after 30 minutes; last entry minute as the book. k = 1 (HOLD1) and k = 2 (HOLD2). No red-candle exit, equal size
for every trade (so a smaller size cannot pass for a better rule), the event's modelled cost.
The comparison leg NOW: today's entry on the same events with the same exits and equal size (buy the open after R,
stop at R's low, 2x target, 30 minutes).
Decided (training): HOLDk PASSES when the lower bound of its mean net PER TRADE is above 0 at alpha = 0.05 / 2.
A rule that only loses less than NOW is reported as that and is not a pass.

## Reported, not decided
Per scenario: events, RUN and OFF medians, LONG net at 5 / 15 / 30 / 60 minutes with win rates, the highest and
lowest point reached within 30 minutes (medians), SHORT net, HOLD1 / HOLD2 / NOW per trade. The same outcomes by
news (yes / no), by alert type, by time of day (first hour / rest), by the price at t0 ($1-2, $2-5, $5-10, above),
by the size of the 1-minute swings before t0 (terciles), and by minutes since the day's high. HOLD: share of
events that trade, share stopped on the entry bar, paired difference to NOW per event (a leg that does not trade
counts 0).

## What follows
- A scenario or HOLD that passes is scored once on the held-out events (2026-07-01 on), unchanged. Only a pass
  there becomes a proposal to the owner (a scenario switch in the live books, or the new entry).
- Nothing passes: recorded with the map itself, which stays as the description of where alerts arrive. Leads in
  the reported splits need their own frozen plan.

## Known limits
RUN and OFF use official bars; live they would come from the free feed's candles (a median 0.55% of the volume),
so a live scenario switch needs its own replay on those candles before it is trusted. The LONG leg has no stop on
purpose: Part A asks where the stock goes, Part B asks how to get in.
