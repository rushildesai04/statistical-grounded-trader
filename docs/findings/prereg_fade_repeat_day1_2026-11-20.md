# Pre-registration: are day-after repeats the losing repeats? (2026-11-20, red-day review)

Status: FROZEN before any held-out outcome is computed. Script: `scripts/fade_repeat_day1_study.py`, run ONCE.

## Source
Red-day review of 2026-11-19 (`red_day_2026-11-19.md`): the repeat gate (amendment F8, no fade on a stock the
channel alerted 1 to 7 calendar days earlier) has stopped 9 alerts since 2026-11-05 whose shorts would have made
+11.8% on average, against a tested -1.01% (training) and -1.33% (held-out). Owner, 2026-11-20: "find solutions to
this attack for all of them".

## What was looked at before this plan (disclosed)
Exploration on the TRAINING events only (2026-11-20): the 317 no-news training repeats split 14 ways by things known
at the alert (days since the prior alert in three bands, whether the prior fade won, lost or hit its stop, one or
more prior alert days, three alert types, three times of day). One cell stood out:

| training no-news repeats | n | average net | wins |
|---|---|---|---|
| alerted the day before (gap 1 calendar day) | 93 | +2.27% | 61% |
| alerted 2 to 7 days before | 224 | -2.37% | 46% |

The difference is about 2.5 standard errors before any correction, and it was the best of 14 cells, so on the
training data alone it proves nothing. The 9 blocked alerts of November do not single it out either (their gaps are
1, 1, 1, 2, 2, 3, 4, 7, 7 days, and they are 7 stock-days, not 9 independent trades). The held-out events have not
been split by gap; only their count was read (outcome-blind): 34 no-news repeats with gap 1 on 29 sessions, 99 with
gap 2 to 7.

## Population and data (no new choices)
The registered repeat test's own files and definitions (`prereg_fade_repeat_days_2026-11-12`):
`reports/fade_news/{dev,holdout}_events.csv`, net = the champion fade (20% stop, 60 minutes, real costs), no-news
fades only (`news_own == 0`), gap = calendar days since the same symbol's previous event in these files. Held-out =
sessions from 2026-07-01.

## The one test (alpha 0.05, one-sided)
Statistic: mean net of held-out day-after repeats (gap 1) minus mean net of held-out later repeats (gap 2 to 7),
sessions resampled jointly, 10,000 resamples, seed 20261112 (`trader_rules_long_study.boot_groups`).

PASS only if ALL: at least 30 day-after repeats; their mean net is above 0; the statistic is above 0 with p < 0.05.

## What each outcome means
- PASS: a candidate amendment, "the repeat gate covers days 2 to 7 only", is put to the owner. Nothing goes live from
  this test alone: playbook section 0 applies (borrow in the record, the 20% loss cap, the owner's explicit OK), and
  the nightly would-have record of blocked repeats is read by gap before any change.
- FAIL, or fewer than 30: the lead is closed. F8 stays as it is. Repeats are not split again on these files.

## Not done
No other gap bands, no other split of the held-out repeats, no sizing variant, no re-run.

## Result (run once, 2026-11-20; `reports/fade_repeat_day1/result.json`) - FAIL, lead closed
| held-out no-news repeats | n | average net | wins |
|---|---|---|---|
| alerted the day before | 34 | -1.56% | 53% |
| alerted 2 to 7 days before | 99 | -1.25% | |

Difference -0.32%, p 0.54. The day-after repeats lose on the unseen events like the later ones: the training cell
was the luck of 14 looks. F8 stays as registered (1 to 7 days); the gate is confirmed on the held-out events for
both bands. Repeats are not split again on these files.
