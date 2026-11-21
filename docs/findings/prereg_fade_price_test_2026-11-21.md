# Pre-registration: does the fade's backtest result sit in stocks under the short-sale price test? (2026-11-21)

Status: FROZEN before any outcome on the training file is computed. Script: `scripts/fade_price_test_study.py`, run ONCE.

Owner, 2026-11-21 (decision 3 A after the execution baseline): yes to a registered check, training file only. The same
day: "do not launch another strategy search or change the frozen rules based on these results". This is a diagnosis of
where a historical average comes from. **It can adopt nothing: no rule, gate or size changes whatever it shows.**

## Why
The execution baseline (`exec_baseline_2026-11-21.md`) re-priced the live record. Split by flags fixed before those
results: forward alerts with no flag averaged -5.2% (37); alerts under the short-sale price test averaged +10.7% (23).
Under the price test a short may not be sold at the bid; the backtest sells every alert at a trade price and does not
model the test. If the backtest's +1.70% lives in those stocks, it is the average of an order a real account cannot
send the way the backtest assumes.

## What was looked at before this plan (disclosed)
The forward numbers above (29 taken shorts and 73 forward alerts, 2026-11-04 to 11-20; overlapping samples). The
training file has never been split by this flag; all that is known of it is its total: 1,335 no-news fades, mean
+1.70%. The held-out file (764 fades, +1.12%) stays closed.

## Population and data (no new choices)
`reports/fade_news/dev_events.csv`, no-news fades (`news_own == 0`), sessions before 2026-07-01, `net` as stored (the
champion fade: 20% stop, 60 minutes, its costs). One row per event; nothing is re-priced.

The flag, per event, with only what was known at the tested entry (the start of the first minute at or after alert +
10 s, `fade.entry_start`):
- TODAY: the lowest low of the regular-hours 1-minute bars COMPLETED before that minute is at or below 0.9 x the
  prior session's close; or
- CARRIED: the prior session's low is at or below 0.9 x the close before it.
Prior closes and lows from unadjusted daily bars of the official tape; intraday lows from the stored 1-minute bars the
backtest itself used. Unknown (no prior daily bar) is reported and left out of both groups.
Known limit, accepted now: a split or reverse split on the prior day mis-sets the flag for that event.

## The one comparison (two-sided, 95%)
D = mean net of flagged events minus mean net of unflagged events. Sessions resampled jointly, 10,000 resamples, seed
20261121; the interval is the 2.5th to 97.5th percentile of D. Each group's mean is reported with the same bootstrap.

| Outcome | Condition |
|---|---|
| TOO FEW | fewer than 100 flagged or fewer than 100 unflagged events |
| SITS IN PRICE-TEST STOCKS | the interval of D is above 0, and the unflagged mean's interval includes 0 or lies below it |
| ALSO OUTSIDE | the interval of D is above 0, and the unflagged mean's interval lies above 0 |
| NOT SHOWN | the interval of D includes 0 |
| REVERSED | the interval of D is below 0 |

## What each outcome means
- SITS IN: the backtest average is carried by stocks where its own assumed order is not allowed. The tested +1.70% may
  then no longer be quoted as the fade's expectation; the score and the texts say "not placeable as tested".
- ALSO OUTSIDE or NOT SHOWN: the forward split is not a feature of the backtest; it stays an observation on 77 alerts.
- REVERSED: the forward split contradicts the backtest; both are reported.
In every case: no rule changes, the alert books stay paper-only, and the forward criteria of the baseline decide.

## Not done
No split by borrow (no history of the lendable list before 2026-11-04), by alert type, price, time of day or year. No
held-out run. No re-run. No limit-order model for shorts under the price test (it needs its own plan).
