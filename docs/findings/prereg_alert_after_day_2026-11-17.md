# Pre-registration: what an alerted stock does after the close (overnight to five days) (2026-11-17)

Status: FROZEN before any daily bar of an alert symbol is fetched for this question and before any number below is
computed. Script `scripts/alert_after_day_study.py`, run ONCE on training; the held-out period is read only for a
cell that is a finding.

## Why
Owner, 2026-11-17: "keep doing these studies ... think about other concepts and ideas like this ... that will really
give us the edge", and earlier the same day: "the longs ... entering at the most optimal time". Every test so far,
the eight of today included, lives inside the hour after the alert. Inside that hour the answer for a long is settled
(`prereg_timing_maps_2026-11-17`: every entry time from 0 to 90 minutes loses after costs). Nothing has looked at
the other clock: the alert DAY as the event, and the nights and days after it.
Two bodies of evidence point opposite ways, which is why it is worth one run:
- attention-driven buying is followed by NEGATIVE returns over the following days (Barber, Huang, Odean and Schwarz:
  the most-bought retail names lose about 5% over the next month): the alert day is a top, a long after it loses;
- rule F8 found that a stock alerted 1 to 7 days earlier does NOT fade on its next alert (-1.0% / -1.3% for the short
  against +2.5%): some of these names keep running for days, and a long at the close would ride that.
Prior: the first is the base rate for small-cap spikes; a tradable long is unlikely (perhaps one chance in ten). A
clear negative drift would also be worth knowing, although shorting it needs shares overnight and stays descriptive.

## Data
Events: the tested fades of training (2025-01-03 .. 2026-06-30), one per symbol-day (the alert day D). Daily bars:
split- and dividend-adjusted (Alpaca `adjustment=all`), fetched once per symbol and cached; every return below is
between two prices of that one adjusted series (no intraday price is mixed in). A symbol without daily bars, or an
event whose needed bars are missing, is counted and left out (delisted names: a known bias in favour of a LONG,
since the stocks that disappear are the ones that fell).

## Map (long from the close of the alert day, unless stated)
Horizons: overnight (close D -> open D+1); the next session (open D+1 -> close D+1); one day (close D -> close
D+1); two days (-> close D+2); five days (-> close D+5).
Groups: all events; no company news / company news that day (`reports/fade_news`); the day closed strong (close in
the top third of the day's range) / weak (bottom third). 25 cells.
Net for a long = return - the event's modelled round-trip spread cost.
**A cell is a finding (long)** when it has at least 300 events, mean net above 0 and the one-sided session-bootstrap
lower bound above 0 at alpha = 0.05 / 25 (10,000 resamples, seed 20261117).
Reported for the short side, not decided (a short held overnight needs borrowed shares every night, which the paper
book cannot check for the past): the same cells with mean return below minus the cost.

## Held-out and what follows
A long finding is scored on the held-out period (2026-07..10; it must keep its sign) and then becomes a
pre-registered forward shadow; nothing here changes a book. No finding: recorded in PLAYBOOK as closed; a long held
past the alert day is not proposed again without new information.

## Known limits
Daily bars only (the close is the last regular trade; a market-on-close order is assumed to get it). Survivorship
as above. Returns are clipped at -95% / +300% per horizon to keep a bad print or an unadjusted corporate action from
deciding a mean; the number of clipped values is reported.

## Result (2026-11-17, training map made once; `reports/alert_after_day/result_train.json`)
Plan `ee88f9f`, code committed before any bar was fetched. Daily bars for 1,279 of 1,280 alert symbols; 2,319 of 2,349
events scored (5 without bars, 25 without the five following sessions). **No cell is a long finding (0 of 25); the
held-out period was not read.** Every cell's mean is at or below zero after costs, and most are far below.

| after the alert day's close | all (2,319) | no news (1,327) | news (988) | closed strong (555) | closed weak (943) |
|---|---|---|---|---|---|
| overnight | -1.12% | -0.65% | -1.81% | +0.58% | -1.46% |
| the next session | -1.20% | -0.85% | -1.65% | -0.03% | -2.49% |
| one day | -2.50% | -1.65% | -3.69% | +0.31% | -4.29% |
| two days | -3.86% | -2.77% | -5.35% | -1.02% | -5.43% |
| five days | **-6.34%** | -5.38% | -7.67% | -2.53% | -7.87% |
(mean return before costs; a long pays about 0.9% more.) All events: the MEDIAN stock is 5.5% lower a day later and
12.6% lower five days later; 33% are higher after one day, 30% after five. The best cell for a long, a strong close
held overnight, is +0.58% before costs and -0.24% after, with a bound from -3.7% to +3.4%: nothing.

Reading:
- The alert day is a top on every clock measured: the hour (the fade), the rest of the day, the night, the week.
  This is the attention effect of the literature at full size (-6.3% in five days), and it holds although the
  names that stopped trading are missing, which flatters a long.
- So the long side on this channel is closed at every horizon from one minute to five days: 27 entry times inside
  the hour and 25 cells after the close, none positive after costs. A long on a stock because this channel alerted
  it is a trade against the strongest regularity in the data.
- The other side is large and NOT claimed: a short from the close would have made about +2.5% in a day and +6.3% in
  five before costs, with means far above medians' mirror because a few names multiply (16 five-day returns above
  +300% were clipped). Holding a short overnight on these names needs shares every night at fees that can exceed the
  drift, and a hard loss cap that a gap ignores (section 0 of the playbook). No borrow history exists to test it.
  The only honest way to learn whether it is real money is forward: record, for each alert day, the IBKR shares
  and fee at the close and on the following days, next to the closes. That is a proposal for the owner, not a result.
