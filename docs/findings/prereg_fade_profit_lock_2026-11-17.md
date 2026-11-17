# Pre-registration: the owner's two-line exit for the fade (2026-11-17)

Status: FROZEN before any number of these variants is computed. Script `scripts/fade_exits.py dev --study lock`, run
ONCE on training. Same gate as `prereg_fade_exits_2026-11-05`.

## Why
Owner, 2026-11-17, on the MPU short (in $1.54 at 10:03; low $1.31 at 10:04, +14.9% open profit; back at the entry by
10:10; official stop at 10:54, about -21%): "the first line and the stoppage should be exactly like you have it ...
once it reaches a minimum, how much X percent will it increase by? And that's when we should sell, still below the
1.54 ... the sellers never took control again". Two lines: line 1 the 20% stop above the entry (unchanged); line 2 a
cover once the price has risen X% off its low while that level is still under the entry, so a trade that was a
winner is not allowed to become a loser.

What is already known, stated before the run:
- The same idea at one distance was tested on 2026-11-05 (X-TRAIL: armed after a 5% gain, cover when a close is 5%
  above the lowest low; 120-minute cap): **-0.18% per trade against the champion's +0.97%** on 2,349 training fades
  (held-out -0.50% against +1.12%); 74% of trades ended on the trail after a median of 9 minutes. It was the worst
  exit tested. Reading then: these stocks chop on the way down, a 5% bounce is noise, and the trail sells winners
  early while the losers still take the full stop.
- Not tested: wider distances, and the trail under the champion's own 60-minute cap. The owner's X is open, so three
  distances are fixed here; none is chosen after seeing a result.
- MPU by these rules (official bars, for illustration only; it is one trade and it is why the rule exists): X 5%
  covers at the 10:07 open $1.42, X 10% at the 10:08 open $1.43, X 15% at the 10:11 open $1.54 (flat).
Prior: a FAIL is more likely than a PASS for X 5% (it is the tested rule with a shorter cap); open for 10% and 15%.

## Variants (all keep the champion's events, entries, costs, 20% stop and 60-minute cap)
Line 2, for X in {5%, 10%, 15%}: the line is ARMED once a bar's low is at or below entry / (1 + X) (from there, a
rise of X% off the low is still below the entry: the owner's "still below 1.54"). Once armed, the first 1-minute
close at or above (lowest low since entry) x (1 + X) is the signal; the cover is the NEXT bar's open (no look inside
a bar; with no next bar inside the cap, that bar's close). The stop is checked first inside every bar (worst case).

| id | X (rise off the low) | armed at a gain of |
|---|---|---|
| LOCK-5 | 5% | 4.8% |
| LOCK-10 | 10% | 9.1% |
| LOCK-15 | 15% | 13.0% |

Implementation: `sgt.fast.exits.Exit(signal="trail", arm=..., give=...)`; the defaults reproduce X-TRAIL exactly.

## Gate (training events 2025-01-03 .. 2026-06-30, n 2,349; run once)
As `prereg_fade_exits_2026-11-05`: metric total net per session; paired daily difference against the champion,
session bootstrap (10,000 resamples, seed 20261105), one-sided lower bound at alpha = 0.05 / 3 (Bonferroni). PASS
needs the lower bound above 0 AND a 1st-percentile per-trade net no worse than the champion's minus 5 points. The
champion computed here must equal `alert_fade_test.score` exactly, or nothing is reported.

## Reported, not decided
Mean / win / 1st percentile; median minutes held; share of trades ended by the line, the stop and the cap; by entry
hour; the held-out period 2026-07..10 (already seen once: secondary). Descriptive: the same on the events the live
book would take today (no news); what line 2 does to trades the champion WON and to trades it LOST (the owner's
claim is about the second group; the cost is in the first).

## What follows
- A PASS: the variant becomes a forward shadow on every alert next to the other challengers; changing the live exit
  needs the owner's OK and an amendment, at forward n >= 300 as for every exit. Not automatic.
- A FAIL: recorded in PLAYBOOK section 4; no variant of X is tried afterwards on these events.
- Longs: a mirror rule is a separate pre-registration on the long book's own trades (its exits differ: stop, 2R
  target, 30 minutes), not decided by this file.

## Known limits
1-minute official bars: the order of prints inside a minute is unknown, so the signal is a close and the fill the
next open. LIVE, the book watches the free feed, which printed 12 one-minute bars in MPU's hour and never showed
the $1.31 low nor the $2.05 high: a line 2 that passes here would still need the official tape live to act as tested.
