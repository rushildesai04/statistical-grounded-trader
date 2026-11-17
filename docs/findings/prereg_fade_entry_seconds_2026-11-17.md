# Pre-registration: how many seconds after the alert should the short be entered? (2026-11-17)

Status: FROZEN before any trade print of an alert minute is fetched for this question and before any number below is
computed. Script `scripts/fade_entry_seconds.py`, run ONCE on training.

## Why
Owner, 2026-11-17: "keep doing these studies ... really think about other concepts and ideas like this ... that will
really give us the edge". The timing maps of that afternoon (`prereg_timing_maps_2026-11-17`) left one fact standing
out: a position opened at the fade's entry is 2.28% lower half an hour later, but one opened five minutes after it
only 0.99% lower. More than half of the half-hour fall happens in the first five minutes. And the rule does not
enter at the alert: it waits for the next 1-minute bar to open, 10 to 70 seconds after the alert (median 40 s,
`reports/telegram/trades_pooled_first.csv`). Entering 2 or 4 MINUTES late is known to lose most of the edge
(`prereg_fade_challengers_2026-11-04`: +0.14% / -0.21% against +0.97%). Nobody has measured the first minute itself:
is the price at alert + 10 s better or worse to short than the next bar's open? Two forces pull opposite ways:
- the fall starts at the alert, so earlier is higher and better;
- followers of the channel buy in the first seconds, so the price may still be rising and the top may come 20 to
  60 s later; and an entry inside the alert minute can land just before a halt, the events that lose most
  (`prereg_fade_halt_audit_2026-11-09`: -4.9% / -7.4% per trade), which the bar rule never trades.
Prior: unknown sign. This is a real question with a mechanism on both sides, on the one book that has a tested edge.

## Data
Events: the 2,349 training fades (status ok) PLUS the training alerts that ran into a halt (`reports/
fade_halt_audit/halt_events.csv`), because an entry inside the alert minute can trade them and the champion cannot
(for those the champion's result is "no trade", 0). Official trade prints (SIP) of the alert's minute and the next
one, fetched once per event and cached (`data/ticks`); official 1-minute bars for the rest of the hour.

## Variants: enter s seconds after the alert, s in {10, 15, 20, 30, 45, 60}
Entry price = the first official print at or after alert + s, if one comes within 10 seconds of that instant (a
market order needs a live print: no print, no trade at that s). The usual filters at the entry: 09:30 to the last
entry minute, $1 to $50. From there the champion's rule: stop 20% above the OWN entry price, cover at the stop or
after 60 minutes (the hour is counted in 1-minute bars from the entry's minute; the entry minute itself is rebuilt
from the prints AFTER the entry, so nothing that traded before the entry can stop the trade). Costs: the event's
modelled round trip + 0.5% locate, as the champion.
The champion is the tested rule on the same events (entry at the next bar's open); it must reproduce its stored net
on the ok events exactly or nothing is reported.

## Gate (training; run once)
Metric: total net per session over ALL events (a leg that does not trade counts 0). Paired daily difference against
the champion, session bootstrap (10,000 resamples, seed 20261117), one-sided lower bound at alpha = 0.05 / 6. PASS
needs the lower bound above 0 AND a 1st-percentile per-trade net no worse than the champion's minus 5 points.

## Reported, not decided
Per s: trades taken, mean per trade, win rate, share stopped; the entry price against the champion's (mean and
median of P(s) / P(bar open) - 1: is the earlier price higher?); the result on the ok events alone and on the halt
events alone (how much of any gain the halts give back); by the alert's second within its minute (does it matter
how long the bar rule made us wait?).

## What follows
- PASS: the held-out period is fetched and scored for the passing s only (secondary), and the variant becomes a
  forward shadow; changing the live entry needs the owner's OK and an amendment (the live book entered mid-minute
  until 2026-11-09 and was moved to the bar on purpose; a return needs the fresh-print guard of this file).
- FAIL: recorded; the bar entry stays; no other s is tried on these events.

## Known limits
A print is not a fill: the first print after alert + s is used as the entry price for every variant and the bar
open for the champion, the same convention on both sides. The halt list is the audit's (alerts with no bar within
5 minutes); an alert followed by a halt later in the hour is in both legs already.

## Result (2026-11-17, training run once; `reports/fade_entry_seconds/result_train.json`)
Plan `a37c9f8`, code `049c9d1`. Prints fetched for 5,348 alert-minutes of 2,470 events (2,349 tested fades + 121
halted alerts), none failed. Champion reproduced `alert_fade_test.score` exactly.

**No variant passes. Entering inside the alert's minute gains nothing in price and loses money in total.**

| enter at | trades | mean/trade | total (units) | daily diff vs champion [99.2% bounds] | gate |
|---|---|---|---|---|---|
| champion: next bar's open | 2,345 | +0.97% | +22.8 | - | - |
| alert + 10 s | 2,279 | +0.49% | +11.2 | -3.30% [-6.44%, -0.39%] | fail |
| alert + 15 s | 2,263 | +0.53% | +12.0 | -3.07% [-5.96%, -0.25%] | fail |
| alert + 20 s | 2,229 | +0.69% | +15.5 | -2.08% [-4.72%, +0.54%] | fail |
| alert + 30 s | 2,182 | +0.82% | +18.0 | -1.38% [-3.95%, +1.23%] | fail |
| alert + 45 s | 2,148 | +0.73% | +15.7 | -2.01% [-4.77%, +0.74%] | fail |
| alert + 60 s | 2,094 | +0.61% | +12.9 | -2.82% [-5.64%, +0.02%] | fail |

Three things were measured that were not known before:
1. **The price does not move, on average, in the first minute after the alert.** Entry price at alert + s against
   the champion's bar open on the same trades: mean +0.04% / +0.07% / 0.00% / +0.02% / -0.11% / -0.19% for s = 10 ..
   60, median 0.00% at every s. Neither force named in the plan shows up: followers do not push it higher first, and
   the fall has not started. The drop the maps found in the first five minutes begins after the first minute. So
   there is no better second to short; the bar's open is as good a price as any.
2. **Entering inside the minute trades the halts.** Of the 121 halted alerts the champion never trades, an entry at
   + 10 s trades 85 and loses 5.5% on each (-4.7 units); at + 30 s 24 (-2.6% each). This is the OMH case, counted.
3. **It misses the thinnest stocks, which are among the best fades.** 151 tested fades had no print within 10
   seconds of alert + 10 s (a market order there has nothing to hit); the champion made +3.44% on those against
   +0.80% on the rest. The bar rule waits for a print and gets them.
On the trades both rules take, the result is the same within noise (+0.72% against +0.80% at + 10 s).

Reading: the 2026-11-09 decision to move the live book from "the latest print at alert + 10 s" to "the first bar
after the alert" was right, and for more than the halts. Speed inside the first minute is not an edge here; being
two or more MINUTES late is what costs. The held-out period was not fetched. No other s is tried.
