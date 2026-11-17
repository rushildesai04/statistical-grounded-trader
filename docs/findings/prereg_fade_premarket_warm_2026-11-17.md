# Pre-registration: skip the short on a stock the channel already alerted before the open that day (2026-11-17)

Status: FROZEN before any number below is computed. Script `scripts/fade_premarket_warm_study.py`, run ONCE.
Same form and pass rule as `prereg_fade_repeat_days_2026-11-12` (rule F8, the one filter that passed).

## Why
Owner, 2026-11-17: "keep doing these studies ... think about other concepts and ideas". Seen on the site's lab that
day: GP's first tradable alert at 09:30 was not its first alert; the channel had already alerted it before the open.
The fade rests on a fresh burst of attention that reverts within the hour. F8 showed what happens when the attention
is not fresh: a stock alerted 1 to 7 days earlier keeps running (no-news shorts -1.0% / -1.3% against +2.5% for the
rest), and skipping those passed training and held-out. F8 looks at earlier DAYS. Nothing looks at earlier the SAME
day: a stock the channel alerted in the pre-market (04:00 to 09:30 ET) has been in play for hours when its first
regular-hours alert arrives. The research rule and the live book treat that alert as the stock's first event.

Two readings, stated before the run:
- warm = a runner already in motion (as F8): the short does worse, skip it;
- warm = a pre-market pump that sells off at the open ("gap and fade"): the short does better.
The registered test is the first (one-sided, as F8). If the data say the second, that is recorded as a lead for a
forward shadow, not as a rule.

## Data
Events: the tested fades with their news flags (`reports/fade_news/dev_events.csv`, `holdout_events.csv`), on the
live rule set: no company news (F6), not a 7-day repeat (F8, `fade_repeat_days_study.mark_repeats`), beta below 3 or
unknown (F7, `reports/fade_beta_unknown/events.csv`). Channel history: every unedited alert in `sgt.db`
(`telegram_alert_test.load_alerts`), all alert types.

## Rule W
`warm` = the same symbol has at least one channel alert of any type posted on the event's calendar day (Eastern)
before 09:30:00 ET. Known the moment the regular-hours alert arrives. Rule W: no short on a warm stock.

## Decision (as F8)
Training (2025-01..2026-06): mean net per trade of kept (not warm) minus skipped (warm) > 0 with p < 0.05 (session
bootstrap of the two groups, 10,000 resamples, `trader_rules_long_study.boot_groups`), at least 100 skipped trades,
and the skipped trades' total net <= 0 (skipping them must not give up money). Only on a training PASS is the
held-out period (2026-07..10) scored: ADOPT-candidate when it has at least 30 skipped trades and the same sign.
An adopt-candidate goes to the owner with its numbers (a live filter needs the owner's OK and an amendment).

## Reported, not decided
The same split on all tested fades and on news fades; by time of day (the first half hour, where warm stocks
concentrate, against later); share of events that are warm; the opposite reading's p.

## What follows
PASS + held-out: proposal to the owner. FAIL: recorded in PLAYBOOK; pre-market alerts are not proposed again as a
filter for the short. A significantly BETTER warm group: noted as a forward-shadow lead only.
