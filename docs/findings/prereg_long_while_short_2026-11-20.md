# Pre-registration: no long on a stock while our own short on it is open (2026-11-20, JMIA, the owner's rule)

Status: FROZEN before any held-out outcome is computed. Script: `scripts/long_while_short_study.py`, run ONCE.

## Source
JMIA, 2026-11-20: the short book was short from $2.78 (11:35 ET, out $2.575 at 12:35, a win) and the long book
bought the same stock at $2.655 at 11:55 and was stopped at $2.615 (12:03). The alert had come three minutes after
the day's high; on the official tape every long entry after it lost within the hour. Owner, asked which entry he
had in mind: "C", that is, "no long while the stock is fading / while our short on it is open".

## What was looked at before this plan (disclosed)
TRAINING alerts only, the bot replay at the live configuration (506 long trades, -0.69% a trade):

| at the long's entry | trades | long, per trade | the rest |
|---|---|---|---|
| our short would be open (a no-news, non-repeat alert, inside the short's 60 minutes) | 179 | -0.95% | -0.54% (n 327) |
| the stock is FADING (the long's entry is below the price at the alert) | 175 | -0.77% | -0.64% (n 331) |
| both (the JMIA case: our short open and in profit) | 66 | -1.12% | -0.62% (n 440) |
| either (the rule as the owner said it) | 288 | -0.80% | -0.53% (n 218) |

"Fading" by itself does not sort the longs (no gradient across six bands of how far the entry is from the alert
price: -0.14%, -1.38%, -0.67%, -0.79%, -0.78%, -0.46%), so it is not part of the rule tested. "Our short is open"
points the owner's way by 0.4 of a point, which 179 trades cannot tell from zero. Also read (training, approximate):
on those 179 alerts the short itself lost -4.06% (it is the alerts where the stock held up that form a base), but
from the long's entry to the short's exit the short made about -0.06%: the long's signal is no reason to cover.

The reason for the rule is not that half point. A real account cannot be long and short one stock at once: at a
broker the buy would simply close the short. Two paper books holding opposite sides of one stock measure something
that cannot be traded, and pay two sets of costs to stand still. The check below asks only whether the unseen trades
contradict that the longs taken while the short is open are no better than the others.

## Population and data (no new choices)
The bot replay's held-out trades at the live configuration (alerts from 2026-07-01, settled, both live setups). A
long is FLAGGED when its alert is one the short book takes by its tested rule (`reports/fade_news` events: no company
news, alert type not news, not alerted in the prior 7 days: `fade_repeat_days_study.mark_repeats`) and the long's
entry is inside the 60 minutes after the short's tested entry bar (`reports/telegram/trades_pooled_first.csv`).
Limits, the same for both groups: borrow, beta and the short's own stop before the long's entry are not in these
files, so some flagged longs are on alerts the live short would not have held at that minute.

## The one test
Statistic: mean net of the held-out longs NOT flagged minus mean net of the flagged ones, sessions resampled jointly
(10,000 resamples, seed 20261112, `trader_rules_long_study.boot_groups`).

PASS only if ALL: at least 30 flagged held-out longs; their mean net is below 0; the statistic is above 0.
The one-sided p is reported, not required (about 150 trades with a spread of 6% a trade cannot resolve half a
point). A PASS says "the unseen trades do not contradict the rule", no more.

## What each outcome means
- PASS: the rule is put to the owner: the long book does not buy a stock while our short on it is open; the alert
  stays listed with what the long would have made, scored nightly, reread at 50.
- FAIL (the flagged longs make money, or do better than the others): the two books keep running side by side on
  paper, and the conflict is settled when real orders are wired (one account, one side).

## Not done
No "fading" rule, no price-versus-alert band, no cover-the-short rule, no other window, no re-run.
