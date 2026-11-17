# Pre-registration: short the squeeze, not the first print (a limit above the market at entry) (2026-11-17)

Status: FROZEN before any number below is computed. Script `scripts/fade_limit_entry_study.py`, run ONCE on training.
Same gate as the exit studies (`prereg_fade_exits_2026-11-05`).

## Why
Owner, 2026-11-17: "obviously we want to max the profit ... same with the longs for that problem of entering at the
most optimal time ... think about other concepts and ideas like this". The exit is closed (three families tested
that day). The entry of the SHORT is the other half of "the most optimal time", and it is the book with a tested
edge. Finding 4 says these stocks squeeze before they fall: a quarter of all shorts are stopped 20% above the entry
and many others trade well above it first. A trader would not sell the first print; he would rest an offer above
the market and let the squeeze come to him, and take the market only if it does not.
What argues against it, stated first: adverse selection. The offer fills exactly when the stock is running up (the
future stops) and misses the stocks that fall at once (the best trades), which are then entered late, and entering
2 to 4 minutes late is known to cost most of the edge (`prereg_fade_challengers_2026-11-04`). Prior: more likely a
FAIL than a PASS; the size of either effect is unknown, which is why it is worth one run.

## Variants (the champion's events, costs, 20% stop and 60-minute hour; only the entry differs)
At the champion's entry bar (open `P0`) rest a sell limit at `L = P0 x (1 + x)` for N minutes:
- filled by the first 1-minute bar starting within N minutes whose high is STRICTLY above L, at L (at that bar's
  open when it opens at or above L); a touch of L does not count as a fill;
- not filled: enter at the open of the first bar starting N minutes or more after the champion's entry (within the
  research's 5-minute tolerance), i.e. a late market entry.
From the entry: stop 20% above the OWN entry price (checked first inside every bar, the fill bar included), cover
after 60 minutes from the entry bar.

| id | x | N |
|---|---|---|
| LIM-2-3 | 2% | 3 min |
| LIM-5-3 | 5% | 3 min |
| LIM-5-5 | 5% | 5 min |
| LIM-10-5 | 10% | 5 min |

## Gate (training events 2025-01-03 .. 2026-06-30, n 2,349; run once)
Metric total net per session; paired daily difference against the champion, session bootstrap (10,000 resamples, seed
20261105), one-sided lower bound at alpha = 0.05 / 4. PASS needs the lower bound above 0 AND a 1st-percentile
per-trade net no worse than the champion's minus 5 points. The champion computed here must equal
`alert_fade_test.score` exactly, or nothing is reported.

## Reported, not decided
Share filled at the limit; mean result of the filled and of the not-filled (late market) trades against the
champion on the same trades (the adverse selection, measured); the same with "not filled = no trade"; held-out
2026-07..10 (secondary); no-news events.

## What follows
PASS: forward shadow, owner's OK and an amendment before any live change (live needs a resting-order simulation on
the free feed). FAIL: recorded in PLAYBOOK section 4; no other x or N is tried on these events.
