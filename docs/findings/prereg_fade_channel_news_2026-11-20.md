# Pre-registration: the channel's own news alert, seconds after the alert we short (2026-11-20, JMIA)

Status: FROZEN before any held-out outcome is computed. Script: `scripts/fade_channel_news_study.py`, run ONCE.

## Source
JMIA, 2026-11-20 (found while monitoring, the trade was open and ahead): the channel alerted "volume increasing" at
11:34:20 ET; our lookup found no company news, so the fade was allowed. At 11:34:45 the channel posted a NEWS alert
for the same stock; our short went in at 11:35:18, 33 seconds after it. The registered rule bars a fade on "a
news-type alert, or company news found at entry" (`prereg_alert_fade_2026-11-01`, amendment F6), because news fades
earn nothing. The code judges the alert that started the trade and our own lookup; a news alert the channel posts
for the same stock between that alert and our entry is not read.

## What was looked at before this plan (disclosed)
TRAINING events only (`reports/fade_news/dev_events.csv`, no-news fades, the champion fade's net), joined to the
channel's news-type alerts (`reports/telegram/trades_news.csv`, 968 rows, each with a headline), five cuts:

| training no-news fades | n | average net | wins |
|---|---|---|---|
| all | 1,238 | +1.84% | 58% |
| a channel news alert within 90 seconds after the alert | 179 | -0.74% | 51% |
| a channel news alert after the alert and before the entry bar (4 to 74 seconds later, median 30) | 90 | -0.42% | 57% |
| a channel news alert during the 60-minute hold, none before the entry | 123 | -3.62% | 41% |
| neither | 1,025 | +2.70% | 60% |

The fades the channel itself tags as news within the hour are where the "no-news" fade has no edge: the lookup
missed the news, the channel did not. The held-out events have not been split this way; only counts were read
(outcome-blind): 487 no-news fades, 36 with a news alert before the entry bar (26 sessions), 40 more during the hold.

## Population and data (no new choices)
The registered no-news test's files and definitions: `reports/fade_news/{dev,holdout}_events.csv` (`news_own == 0`,
alert type not news, net = the champion fade: 20% stop, 60 minutes, real costs), the tested entry bar from
`reports/telegram/trades_pooled_first.csv`, the channel's news alerts from `reports/telegram/trades_news.csv`.
Held-out = sessions from 2026-07-01. A fade is FLAGGED when the channel posted a news alert for the same stock on
the same session after the fade's own alert and before the end of its 60-minute hold.

## The one test (alpha 0.05, one-sided)
Statistic: mean net of the held-out flagged fades minus mean net of the held-out unflagged fades, sessions resampled
jointly (10,000 resamples, seed 20261112, `trader_rules_long_study.boot_groups`).

PASS only if ALL: at least 30 flagged fades; the statistic is below 0 with p < 0.05; and the flagged fades whose news
alert came BEFORE the entry bar (the part a rule can act on at the entry) have a mean net below the unflagged mean
(a consistency condition, no significance asked of 36 trades).

Why the test is on both parts together: the entry part alone (36 held-out fades with a spread of about 13% a trade)
cannot resolve two points. The premise is one claim, "a stock the channel tags as news within the hour is a news
fade", and it is tested with the power the data has. The part during the hold uses information that arrives after
the entry: it supports the premise, it is not a tradeable rule as measured.

## What each outcome means
- PASS: (1) a candidate entry gate is put to the owner: no fade when the channel has posted a news alert for the
  stock between the alert and our entry (it is news under F6; the alert stays listed with what the short would have
  made; nothing goes live without the owner's word). (2) "Cover when the channel posts a news alert during the hold"
  gets its own registered study on minute bars before anything is said about it.
- FAIL: closed. The code stays as it is. The channel's news alerts are not split again on these files.

## Not done
No other window, no other alert type, no headline reading, no exit simulated, no re-run.
