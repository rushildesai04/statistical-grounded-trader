# Pre-registration: when to leave a short, when to enter a long, mapped on every tested alert (2026-11-17)

Status: FROZEN before any number below is computed. Script `scripts/fade_timing_map.py`, run ONCE on training; the
held-out period is opened only for a cell that passes on training.

## Why
Owner, 2026-11-17 15:00 ET: "for shorts, once a stock reaches its lowest and keeps rising for a certain amount of time
or for a certain amount and we still have made profit, get out ... we want to max the profit but get out at the right
time instead of waiting too long or being overconfident. Same with the longs for that problem of entering at the most
optimal time".

Two of this idea's forms were tested the same day and failed: the price form (cover X% off the low while under the
entry, X 5 / 10 / 15%: `prereg_fade_profit_lock_2026-11-17`) and the clock-on-losers form (leave a losing short at
half time: `prereg_fade_time_cut_2026-11-17`). Six earlier exits that react to the price also failed, and so did a
model trained to pick the exit (`prereg_fade_exit_learn_2026-11-06`). Testing one more hand-picked threshold on the
same 2,349 trades is how a false "pass" gets found. So this file does not test another threshold. It measures the
whole question once, as a map, and fixes in advance what would count as a finding:
- shorts: every moment "the low is N minutes old, price is above it, and the short is still in profit", and what
  the trade made from there if held (the continuation), against leaving at that moment;
- longs: every entry time after an alert, and what the next 30 minutes made after costs.
Priors, stated first: the tested average path keeps falling for 60 to 90 minutes (short net by exit time: 15 min
+0.18%, 30 +0.65%, 60 +0.97%, 90 +1.14%), so the expected answer is "holding is better in every cell" and "no long
entry time is positive". If a cell says otherwise at the level below, it is a real lead; if none does, the exit and
the long entry time are closed questions on this channel.

## Data
The fade's tested events (`reports/telegram/trades_pooled_first.csv`, status ok), official 1-minute bars, the
champion's entry (bar open at alert + 10 s), 20% stop and 60-minute cap (`sgt.fast.exits`). Training 2025-01-03 ..
2026-06-30 (2,349 events); held-out 2026-07..10 (764).

## Map A, shorts: leave when the low has gone stale?
For each event, on each closed bar while the champion still holds the trade: `stale` = minutes since the bar that
made the lowest low since entry. For N in {5, 10, 15, 20}: the FIRST bar whose close has `stale >= N` and is below
the entry (the short is in profit) is that event's moment for N. Covering there = the next bar's open. Bucket by the
profit at that moment (entry - close) / entry: 0 to 5%, 5 to 10%, 10% or more. 12 cells.
Per cell: events, mean result if covered there, mean result of the same trades held as the champion holds them
(stop and hour included), and the difference `hold - cover` (the continuation).
**A cell is a finding** when it has at least 150 events, its mean continuation is below 0, and the one-sided
session-bootstrap upper bound of the continuation is below 0 at alpha = 0.05 / 12 (10,000 resamples, seed 20261117).
Also reported as whole rules (no decision): STALL-N for each N (all buckets together), mean per trade against the
champion's on all events.

## Map B, longs: is any entry time positive?
For each event and k in {0, 5, 10, 15, 20, 30, 45, 60, 90} minutes after the fade's entry bar: buy the open of the
first bar at or after that time (skipped when no bar prints within 5 minutes of it, or after 15:30 ET), sell the
last close within 30 minutes (or the session's last bar). No stop and no target: this asks only whether the timing
is right. Net = move - the event's round-trip spread cost. Bucket by where the price is against the alert-time entry
price: down 10% or more, down less than 10%, above it. 27 cells.
**A cell is a finding** when it has at least 300 events, mean net above 0, and the one-sided session-bootstrap lower
bound above 0 at alpha = 0.05 / 27.

## Held-out and what follows
Only a cell that is a finding on training is computed on the held-out period (same statistic; it must keep its sign).
A cell that holds on both becomes a pre-registered forward shadow on every live alert, decided at 300 forward
events like every other challenger; nothing changes a live rule from this file. No finding: recorded in PLAYBOOK
section 3 as closed, and neither an exit that leaves a winning short early nor a long entry time on these alerts is
proposed again without new information (a new data source, not a new threshold).

## Known limits
1-minute official bars; a cover or an entry at the next bar's open. Costs as modelled. The map looks at many cells:
the Bonferroni levels above are the guard, and a finding still has to hold out of sample and forward.
