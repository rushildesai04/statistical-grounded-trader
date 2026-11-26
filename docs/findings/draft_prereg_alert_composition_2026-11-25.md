# DRAFT registration: are the forward alerts the same kind of alert as the tested ones? (composition, no outcomes)

**Status: DRAFT FOR THE OWNER'S REVIEW. Not registered, not frozen, no script written, nothing computed.** Owner,
2026-11-25: "draft both registrations for review, not execution ... P2 must specify point-in-time features,
missing-data handling, comparisons, and what decision a composition difference would inform. Outcome-blind analysis
can identify differences, not establish their effect on profitability." It becomes a registration only when the
owner says so; until then every number in it is a proposal.

## 1. Question

The fade's tested figures come from alerts of 2024-01 to 2026-10. The forward record starts 2026-10-30. Are the
forward alerts the same population, measured only by things known when each alert arrived?

## 2. What a difference would inform, and what it cannot

| It can inform | It cannot |
|---|---|
| Whether "+1.12% tested" is a like-for-like reference for the forward record when amendment F1 is read at 300, and for any monitor that uses the tested figures as its reference | say whether a difference helps or hurts profit. No outcome is loaded |
| Whether a re-weighted reference (the tested result weighted to the forward mix) is worth registering as its own, separate analysis | adopt, propose or justify a filter, a gate or a size. A feature that differs is not a feature that matters |
| Which features to record prospectively if the mix has moved | change amendment F1, the cohort review, H1 or any registered figure |

If it finds no notable difference: the tested figures stand as the reference, and the forward shortfall or surplus
is not attributable to a changed mix on these features. If it finds one: that is reported as a difference in the
population, with the statement that its effect on results is unknown and would need its own registered test on data
this check did not use.

## 3. Populations (fixed lists, no new choices)

| Name | Events | Source |
|---|---|---|
| TRAIN | first in-cell alert per symbol-day, status ok, sessions 2024-01 to 2026-06 (2,349 rows) | `reports/telegram/trades_pooled_first.csv`, `split == "train"` |
| HELD | the same, sessions 2026-07-01 to 2026-10-29 (764 rows) | the same file, `split == "holdout"` |
| FWD | the forward record's events (`alert_fade_forward.forward_events`, status ok), sessions from 2026-10-30 to the last complete session before the registration is frozen | the list is written to a file at the freeze and not extended |

**For the owner to decide before the freeze:** whether HELD may be read at all for this. No outcome column of it is
loaded, but its rows are. The alternative is TRAIN as the only reference, which loses the yardstick of section 6.
Recommended: read it, features only, with the column whitelist of section 7 enforced by a test.

## 4. Features: each known at the alert, each from the same source in every period

All prices and volumes are from the stored official one-minute bars of the alert day (`data/alpaca`, pre-market
included), using ONLY bars that closed before the alert's minute began. Nothing after the alert is read.

| # | Feature | Definition | Bands (fixed here) |
|---|---|---|---|
| 1 | Alert kind | the parsed cell of the message (`alert_type`) | as parsed |
| 2 | Time of day | the alert's ET time | 09:30-10:00, 10:00-11:30, 11:30-14:00, 14:00-15:44 |
| 3 | Price | close of the last bar before the alert minute | $1-2, $2-5, $5-10, $10-50 (the stop study's bands) |
| 4 | Move from the session's open | that close / the 09:30 bar's open - 1 | under 0, 0-10%, 10-30%, 30-100%, over 100% |
| 5 | Run into the alert | that close / the close 5 bars earlier - 1 | under 0, 0-3%, 3-10%, over 10% |
| 6 | Dollar volume before the alert | sum of close x volume, regular hours, before the alert minute | under $1m, $1-5m, $5-25m, over $25m |
| 7 | Alerts that session | rank of the alert among the session's in-cell alerts (1 = first) | 1-3, 4-8, 9+ |
| 8 | Seen before | the symbol had an in-cell alert in the prior 10 sessions | yes / no |

Secondary, reported apart because it needs a second source: move from the prior close (the prior session's close
from the daily bars `sgt.fast.beta` reads; unadjusted price for the ratio). In a spot check of six historical events
on 2026-11-25, the alert day's bars were present for all six and three had no minute bars in the prior week, so the
prior close cannot come from the minute store.

**Left out, with the reason:** company news (the history's flag and the live lookup are different instruments);
borrow listing (no list exists before 2026-11-04); beta (changes slowly, and it is already a live gate, so the
forward mix on it is selected by the rule, not by the channel); halts (no halt feed).

Price arithmetic: `price-decimal-v1` (band edges compared as exact decimals; a touch belongs to the upper band).

## 5. Missing data

- A feature that cannot be computed for an event (no bar before the alert minute, fewer than 5 bars for feature 5,
  no 09:30 bar yet for feature 4 on a pre-open alert) is the category MISSING for that feature. It is counted per
  population and shown; it is never imputed and the event is never dropped from the other features.
- If MISSING exceeds 10% of any population for a feature, that feature is reported as NOT COMPARABLE and no
  difference statement is made for it.
- An event whose bars cannot be read at all is listed by symbol and session; if more than 5% of a population is in
  that list, the whole check is INVALID and says so.

## 6. Comparisons

For each feature, three pairs: FWD against TRAIN, FWD against HELD, and HELD against TRAIN. The third is the
yardstick: it shows how much the mix moved between two historical periods in which the tested result was observed
to hold.

- Statistic: the total variation distance between the two band distributions (half the sum of absolute differences
  in band shares), and the band shares themselves.
- Uncertainty: 10,000 resamples of whole sessions within each population (seed 20261125), 95% interval for each
  share and for the distance.
- A difference is called NOTABLE for a feature only if both hold: (a) the FWD-TRAIN distance is larger than the
  HELD-TRAIN distance, and (b) the interval of (FWD-TRAIN distance minus HELD-TRAIN distance), at 99.4% (0.05 / 8
  features), excludes zero. Otherwise: "no notable difference at this sample size".
- Reported beside every statement: the smallest share difference this sample could have shown. With about 120 to
  150 forward alerts in 18 to 25 sessions that is on the order of 12 points for a band holding a third of the
  alerts. A "no notable difference" at this size is weak, and the report says so in those words.

## 7. Keeping it blind

- The script loads the columns `symbol, alert_type, posted_at, session, status, split, cell` and nothing else from
  the events files. A test fails the build if any column whose name begins `gross`, `net`, `cover`, `exit` or `cost`
  is requested.
- Bars are read only up to the alert minute. A test feeds a day whose later bars are poisoned and requires an
  identical result.
- One run. Its output is stored; the run is entered in the trial ledger. No band, feature or threshold is changed
  after the freeze. A second look needs a new registration.

## 8. Limits known now

One channel. A parser that has changed over time may itself move feature 1. The forward list is small. The check
sees eight features; a population can differ on something not listed. The bands are choices: they are fixed here so
that they are not chosen after seeing the forward mix.

## 9. Effort and cost

One script and its tests, about half a working session, one independent review of the registration before the
freeze. No new data, no paid data, no outside request: every bar is already stored.
