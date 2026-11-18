# Pre-registration: is the best long entry EXACTLY when EMA 9 crosses 21, and what does a minute of delay cost? (2026-11-18)

Status: FROZEN before the script exists and before any number below is computed. Script
`scripts/ema_exact_cross_study.py`, run ONCE on training. Report only: nothing here changes a book.

## Why
Owner, 2026-11-18, passing on a trader's comment: "the buy signal seems to be coming in too late. When there's
volume rising, the best entry is EXACTLY when 9 crosses 21 ema. If you wait too long, then most of the opportunity
is gone ... If there's even 1 min delay, the trade can get more risky."

What is already measured:
- `ema_cross_check_2026-11-05`: EMA 9/21 cross-ups seen at a bar's CLOSE and bought at the next bar's open:
  46,711 signals, gross -0.01% to +0.01% at 5 to 60 minutes; with volume >= 3x, 6,708 signals, +0.02% (15 min),
  +0.08% (30 min); round trip 0.7%. That test enters up to a minute after the cross, the very thing the comment
  objects to.
- `reports/decision/intrabar.report.md`: the trader's full entry replayed on official prints, bought one second
  after EMA 9 pierced EMA 24 inside the minute: 2,742 entries, gross -0.17% (5-minute hold), net -0.98%; the
  same entries at that bar's close would have been 0.05% worse on average (median 0). That test uses 9/24 and four
  extra conditions (volume spike 5x, MACD, PVT, RSI), not the plain cross the comment describes.
Not measured: the plain 9/21 cross with rising volume, bought at the exact price of the cross, against the same
events bought one, two and five minutes later.

## The exact cross price (no prints needed)
With completed-bar values e9 and e21 after minute i-1 (EMAs of 1-minute closes over the whole session, pre-market
included, as `trader_rules.indicators`), the chart's live EMA 9 is above the live EMA 21 inside minute i exactly
when the last price p is above
    P* = ((1 - a21) * e21 - (1 - a9) * e9) / (a9 - a21),   a9 = 2/10, a21 = 2/22.
Event: minute i in regular hours with e9 <= e21 after minute i-1 and high[i] >= P*. The cross happens inside
minute i at the first price at or above P*.

## Entries (long), on every event
- EXACT: max(P*, open[i]): the cross price itself, no latency, no slippage. Nobody can do better; a real order
  fills at or above it. This is the comment's claim at its most favourable.
- NEXT: open[i+1] (what a system that waits for the bar to close does, up to a minute late).
- PLUS1, PLUS2, PLUS5: open[i+2], open[i+3], open[i+6].
Marks: the close of minute i+5, i+15 and i+30 (the same clock time for every entry, capped at the session's last
regular bar; an entry that would come at or after its mark is dropped from that horizon for all entries). Cost: the
measured gapper round trip (`GAPPER_COST_MODEL`, as `ema_cross_check`) at the entry.
A cross that has failed by the close of minute i (e9 <= e21 again) is a "fake-out": EXACT buys it, a bar-close
system never sees it. Reported: its share, and the result of the confirmed events alone.

## Volume rules (two, fixed here)
- RISING (tradeable, primary): the completed minute i-1 traded at least 2x the mean of the 10 minutes before it:
  known before the cross.
- SPIKE (look-ahead, upper bound): minute i itself ends at 3x or more of its 10-minute base (the rule of
  `ema_cross_check`). At the instant of the cross this is not yet known; it is reported because it is the most
  generous reading of "volume rising".
- ALL: every event, for reference.

## Data
Gapper universe (`data/universe/gappers_2y.csv`, prior-day volume >= 50k), sessions before 2026-04-01 (the same
universe and training period as both earlier tests; later sessions stay closed), official 1-minute bars, regular
hours.

## Gate (training; run once)
Claim A, "the exact cross is a profitable entry": PASS if, on RISING events, the mean net (gross - cost) of EXACT
has a session-bootstrap one-sided lower bound above 0 at alpha = 0.05 / 3 for at least one of the three marks
(10,000 resamples, seed 20261118; a bound that cannot be computed counts as a fail).
Claim B, "a minute of delay costs the opportunity": measured, not gated, as entry price NEXT / EXACT - 1 and
PLUS1 / EXACT - 1 on the same events (mean with its 95% session-bootstrap interval, median, share above 0), next
to the 0.7% cost of the trade and to the gross move after EXACT.

## Reported, not decided
Per volume rule and entry: events, mean gross and net at each mark, win rate after costs; fake-out share and the
result of EXACT on fake-outs and on confirmed crosses; by time of day (09:30-10:29, 10:30-14:59, 15:00-15:54);
the delay cost by the same splits.

## What follows
- A PASS: the passing rule is replayed on official prints with one second of latency (the realistic fill) before
  anything else; only that replay, then the held-out period, could lead to a shadow. The live long book stays
  measure-only either way (owner, 2026-11-17).
- A FAIL: recorded in the playbook; finding 10 (chart signals carry no edge here) stands with the exact-entry
  version added; no other span, volume threshold or horizon is tried on these events.

## Known limits
EXACT is an upper bound: it assumes a fill at the cross price with no spread crossed and no latency; the cost
model charges the usual round trip on top. P* treats the minute as one continuous path: when a minute gaps over
P* between prints, the real first price above it is higher (again in EXACT's favour). Several crosses of one stock
in one session all count; the session bootstrap covers their dependence.

## Amendment 1 (2026-11-18, before the script exists and before any number): the mechanics made exact
The sentence in "Entries" about dropped entries was loose (read literally it removes the 5-minute mark, because
PLUS5 always enters after it). It is replaced by:
- A thin stock has minutes with no trade. "open[i+k]" is the open of the first regular-hours bar at or after k
  minutes past minute i; the mark at h is the close of the last regular-hours bar at or before h minutes past
  minute i.
- An event needs minute i + 6 at or before the session's last regular bar. PLUS5 is not scored at the 5-minute
  mark. At a mark, an event is kept only when every entry scored there has its bar at or before the mark's bar, so
  all entries are compared on the same events.

## Result (2026-11-18, training run once; `reports/ema_exact_cross/result_train.json`)
Plan `3ef80d8` + amendment `8cb03a9`, code `8989b7c`. 69,720 exact cross-ups, 2,194 stocks, 375 sessions.
**Claim A: FAIL.** RISING + EXACT net: -0.83% (5 min), -0.87% (15), -0.87% (30); lower bounds -0.89%, -0.96%, -1.00%.

| volume rule | events | fake-outs | EXACT gross 5 / 15 / 30 min | EXACT net (30 min) | NEXT gross 30 min | NEXT price vs EXACT (mean [95%], median) |
|---|---|---|---|---|---|---|
| ALL | 69,720 | 34.2% | -0.06% / -0.09% / -0.09% | -0.79% | -0.03% | -0.05% [-0.06, -0.04], -0.01% |
| RISING (tradeable) | 11,554 | 30.5% | -0.10% / -0.14% / -0.14% | -0.87% | -0.10% | -0.04% [-0.06, -0.01], 0.00% |
| SPIKE (look-ahead) | 9,686 | 32.0% | +0.26% / +0.29% / +0.29% | -0.46% | -0.02% | +0.31% [+0.27, +0.35], +0.15% |

Fake-outs against crosses that held (EXACT gross at 30 min): ALL -0.70% / +0.23%; RISING -1.00% / +0.24%;
SPIKE -1.08% / +0.93% (net +0.19% on the 6,579 that held; the same events bought a minute later: +0.04% gross).

Reading:
1. Bought at the exact cross price with no delay and no slippage, the plain 9/21 cross loses before costs
   (-0.06% to -0.14%) and 0.8% to 0.9% after them, with or without rising volume known at the time. Win rate
   after costs 24% to 35%. No time of day is positive (best: 10:30-14:59, -0.03% gross at 30 minutes).
2. A minute of delay does not cost anything on what can be known at the cross: the next minute opens 0.04% to
   0.05% BELOW the cross price on average (median 0; higher in 44% of cases). The reason is the fake-outs: about
   one cross in three is undone before its own minute closes, and those lose 0.7% to 1.1% within half an hour.
3. The comment is right about what a finished chart shows. A chart only draws the crosses that held. On those,
   with a big volume bar, the cross price is 0.93% below the price 30 minutes later and the whole move is inside
   the cross minute: a minute later the same events are flat (+0.04%). So "exactly at the cross was the best entry
   and a minute later it was gone" is true of every cross one can see afterwards. It cannot be bought: at the
   instant of the cross neither the final volume of the minute nor whether the cross holds is known, and buying
   every cross that looks the same at that instant pays for the winners with the fake-outs (SPIKE, both kinds
   together: +0.29% gross against 0.75% of costs, and that still uses the minute's final volume).
4. The tradeable form of "volume already exploding at the instant of the cross" was replayed on official prints
   on 2026-10-19 (`reports/decision/intrabar.report.md`, one second of latency, 2,742 entries): gross -0.17%, net
   -0.98%; waiting for the bar's close cost 0.05% on average. The two tests agree.
5. Data speed is not the limit: EXACT is the result of a perfect, instant feed and fill, and it loses. The live
   book reads real-time free prints (one exchange, thin) and decides on closed 1-minute bars every 30 seconds;
   a paid full real-time tape would move its entries by seconds to a minute, which item 2 prices at about zero.

Verdict: recorded; finding 10 of the playbook stands with the exact-entry version added. No other span, volume
threshold or horizon is tried on these events.
