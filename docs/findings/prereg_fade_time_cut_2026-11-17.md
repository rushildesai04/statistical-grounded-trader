# Pre-registration: cut a fade that is not working at half time (2026-11-17)

Status: FROZEN before any number of these variants is computed. Script `scripts/fade_exits.py dev --study cut`, run
ONCE on training. Same gate as `prereg_fade_exits_2026-11-05` and `prereg_fade_profit_lock_2026-11-17`.

## Why
Owner, 2026-11-17 afternoon: "it still waits too long in many scenarios because it is confident the price will
increase but the sellers end up taking control over time smoothly. Same for shorts ... feel free to challenge me".
The live record of that hour (read-only, 2026-11-17 14:52 ET):
- Longs: 15 live trades; 11 stopped after a median of ONE minute (longest 11 minutes), 4 hit their target. None ever
  reached its 30-minute time exit. For the long book "waits too long" is not what happens: it is stopped at once.
- Shorts: 22 settled trades; 5 hit the 20% stop (after 6 to 45 minutes), 9 timer exits won (+11.0% on average) and
  **8 timer exits lost (-6.4% on average)**: trades that never hit the stop and ground against the short for the
  whole hour. This is the owner's case, and it is real: MPU and XLAB are of this kind.

What is already tested about getting out earlier when a short turns (all on 2,349 training fades, champion +0.97%):
30-minute hold for every trade +0.65%; cover on a VWAP reclaim +0.52%; cover on an EMA 9/21 cross up +0.68%; cover
when a pullback fails +0.87%; breakeven exit after 5% underwater +0.38%; learned exit model +0.81%; trailing 5%
-0.18%; the two-line profit lock -0.21% / +0.40% / +0.47%. Every one reacts to a PRICE pattern and every one lost.
Not tested: a plain clock check, "at half time, is this trade losing? then leave", which reacts to no bounce and
leaves winners alone by construction. Finding 7 warns against it (fades that went 5% against us and came back to the
entry then earned +2.3% / +4.2% more), but that is about trades that have already come back; a trade still losing
at half time is a different group and its remaining half hour has never been measured.
Prior: open. The cut saves the slow grinders and the late stops; it costs the trades that are under water at half
time and fall in the second half (the usual squeeze-then-fade path).

## Variants (all keep the champion's events, entries, costs, 20% stop and 60-minute cap)
At the first 1-minute bar that closes at or after T minutes from the entry bar's start, once: if that close is at or
above entry x (1 + L), cover at the NEXT bar's open (with no next bar inside the cap, that bar's close). Otherwise
hold as the champion does. The stop is checked first inside every bar.

| id | T | L (how far against the short) |
|---|---|---|
| CUT-20 | 20 min | 0% (any loss before costs) |
| CUT-30 | 30 min | 0% |
| CUT-30-5 | 30 min | 5% (clearly not working) |

Implementation: `sgt.fast.exits.Exit(signal="cut", cut_min=T, cut_loss=L)`.

## Gate (training events 2025-01-03 .. 2026-06-30, n 2,349; run once)
Metric total net per session; paired daily difference against the champion, session bootstrap (10,000 resamples, seed
20261105), one-sided lower bound at alpha = 0.05 / 3. PASS needs the lower bound above 0 AND a 1st-percentile
per-trade net no worse than the champion's minus 5 points. The champion computed here must equal
`alert_fade_test.score` exactly, or nothing is reported.

## Reported, not decided
Mean / win / 1st percentile; share ended by the cut, the stop and the hour; held-out 2026-07..10 (secondary); no-news
events; what the cut does to trades the champion won and to those it lost; and the group the rule is about: trades
that are losing at half time, what the champion made on them from there.

## What follows
A PASS: forward shadow next to the other challengers, owner's OK and an amendment before any live change. A FAIL:
recorded in PLAYBOOK section 4; no other T or L is tried on these events. Longs: no test (the record above: they do
not wait).
