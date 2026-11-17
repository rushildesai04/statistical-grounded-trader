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
