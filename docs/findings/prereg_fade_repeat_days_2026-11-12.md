# Pre-registration: skip fades on stocks the channel alerted in the prior 7 days (11-12 loss review)

Status: FROZEN before any outcome is computed. Script: `scripts/fade_repeat_days_study.py`, run ONCE.

## Source
2026-11-12 deep review (owner: "think about every possible way to make this better ... from the wrong trades and what
worked on the right ones"). RETO was faded 11-11 (+5.8%) and again 11-12 (-21%); STAK three times in six days
(+7.1%, -5.0%, -8.8%). A stock the channel keeps alerting may be a multi-day runner that squeezes shorts.

## Known (not re-tested)
A repeat alert on the SAME day after 60 minutes loses as a fade (-3.19%, prereg_fade_later_alerts_2026-11-06); the
live book trades the first event per symbol-day only. Repeats across days were never tested.

## Population and data (no new choices)
The champion fades of `reports/fade_news/{dev,holdout}_events.csv` (net = 20% stop, 60 minutes, real costs), dev =
training (sessions before 2026-07-01), holdout = from 2026-07-01. Primary population = no-news fades (news_own == 0),
the live book's population (amendment F6). A fade is a REPEAT when the same symbol has an event in these files 1 to 7
calendar days before its session (outcome-blind count: dev no-news 317 of 1,335; held-out 133 of 504).

## Candidate (one; alpha 0.05 one-sided)
**RP**: skip repeats. Statistic: mean net of kept (non-repeat) minus mean net of skipped (repeat) no-news training
fades, sessions resampled jointly (10,000 resamples, seed 20261112). Minimum 100 skipped.
ADOPT (live fade book, owner's OK) only if training statistic > 0 with p < 0.05 AND held-out no-news statistic > 0
(held-out opened only on a training pass; n skipped >= 30), AND skipping does not lower the book's total: training
mean net per session of kept trades >= the champion's (a filter that drops winners for nothing is not adopted).
Secondary (never decides): all fades (news included); repeats within 1-3 days.

## Not done
No other windows (1-3, 14, 30 days decide nothing). No sizing variant.

## Result (run once, 2026-11-12; `reports/fade_repeat_days/result.json`) - PASS, ADOPTED as amendment F8
| no-news fades | kept (no alert in prior 7 days) | skipped (repeat) | difference | p |
|---|---|---|---|---|
| training | +2.54% (n 1,018) | -1.01% (n 317; total -3.20) | +3.55% | 0.0001 |
| held-out | +2.55% (n 371) | -1.33% (n 133; total -1.77) | +3.88% | 0.005 |
Secondary: all fades (news included) training +1.36% vs -0.68% (p 0.006); repeats within 1-3 days only +1.92% vs
+0.39% (p 0.11): the 4-7-day repeats carry the effect too, so the 7-day window stays as registered.
Reading: a stock the channel already alerted in the past week is a multi-day runner; shorting it again loses on
average, and skipping those trades raises the book's total in both periods. Live (Nov 4-12, partial history): F8
would have skipped STAK 11-06 (-5.0%), STAK 11-10 (-8.8%) and RETO 11-12 (-21.0%); the 18 kept fades average -2.0%.
Live rule (owner: "improve the algorithm in any way possible only if it makes sense"; adopted with the PR merge):
`fade.prior_alert` counts alerts that were tested events (passed the cell, window and price filters and had an entry
bar, including those the short skipped for news, beta or F8); the long book still treats F8-skipped alerts as events.
