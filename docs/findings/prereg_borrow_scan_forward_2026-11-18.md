# Pre-registration: the stock-loan list as a scanner (shares drying up, fees jumping), forward only (2026-11-18)

Status: FROZEN before the scanning code exists and before any stored list has been compared across days for this
question. Decided once, on the lists stored from 2026-11-04 on.

## Why
Owner, 2026-11-18: "let's do both" (see `prereg_gap_news_2026-11-18`). The runner has stored the broker's full
stock-loan list (about 19,000 symbols: shares available, annual fee) every 15 minutes since 2026-11-04
(`data/borrow`). Nobody on the alert channel trades off it. Two readings exist and they point opposite ways:
shares vanish because short sellers who know something are leaning on the stock (it falls), or because the
stock is crowded and a squeeze is near (it rises). The data decides; both directions are scored.

## Events (from each session's LAST list at or before 16:00 ET against the previous session's)
For symbols priced at least $2 with at least $2 million traded that day:
- DRY: shares available fell by 90% or more from the previous session's closing list, from at least 100,000 to
  under 10,000.
- FEE: the annual fee at least doubled from the previous session's closing list and is now at least 20%.
A symbol counts once per kind in any 10 sessions.

## The trade that scores an event
Enter at the event day's close, leave at the close 1 session and 5 sessions later; result = stock return minus
SPY's over the same days, scored long and short. Cost 0.30% a round trip; for the short leg also the fee at the
event, pro rata for the days held.

## Decided (once)
At the first night on which either kind has 300 events with a 5-session result: for each kind and each of the two
holds, long and short (8 tests, alpha = 0.05 / 8, bootstrap by event date, 10,000 resamples, seed 20261118; no
bound = fail): PASS when the lower bound of the mean net is above 0. Until that night only event COUNTS are read.

## What follows
- A pass: put to the owner as a scanner signal (the evening list of that kind, texted, paper-traded as scored).
- No pass: recorded; the list stays what it is today, the check that a short can really be borrowed.

## Known limits
About two weeks of lists exist; the decision is some weeks away (the count is reported nightly). One broker's
list is not the whole lending market. A short on a stock whose shares have just vanished cannot be opened: the
short leg of DRY is scored for understanding, not as a trade that could be done.
