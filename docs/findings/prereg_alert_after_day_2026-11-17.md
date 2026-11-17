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
