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
