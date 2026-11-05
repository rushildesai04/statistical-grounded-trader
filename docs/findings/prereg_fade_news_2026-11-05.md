# Pre-registration: does fresh company news predict losing fades? (2026-11-05)

Committed BEFORE any news feature below is joined to a fade outcome.

## Why
Live trade 2026-11-05 DNUT (volume_increasing, alert 10:11:54 ET): short ~$4.47, out at the 60-minute exit ~$4.64,
about -4% gross. At the tested timing (open of the 10:13 bar, $4.455) the result is the same, so the 58-second
listener delay did not cause it. The stock opened +0% and climbed steadily all morning (+23% at the alert, +29% an
hour later) after Q3 earnings at 06:46 ET: EPS and sales beat, full-year sales guidance raised. A pump with no news
tends to fade; a re-rating on real results tends to keep drifting (post-earnings drift). The confidence study
(prereg_fade_confidence) found the alert-time price/volume features carry no per-trade signal and named news text
as the next information to test. This is that test.

## Data
Events: `reports/telegram/trades_pooled_first.csv`, status ok. Development = split `train` (2025-01-03 ..
2026-06-30, 2,349 events). Held-out = split `holdout` (2026-07-01 .. 2026-10-29, 764 events): the same events the
frozen fade rule was confirmed on, never joined to news. Outcome = the frozen rule's net
(`alert_fade_test.score`: 20% stop, 60-min exit, gapper cost + 0.5% locate), identical to every earlier fade study.
News: local Benzinga archive `data/news/*.parquet` (Alpaca news API, all symbols), point in time by `created_at`.

## Features (point in time; only articles created strictly before the alert)
Window = from 16:00 ET of the previous weekday to `posted_at`.
- `news_any`: at least one article whose `symbols` lists the event symbol.
- `news_own`: at least one such article listing at most 2 symbols (company-specific, not a market roundup).
- `news_earn`: a `news_own` article whose headline matches (case-insensitive)
  `earnings|eps|revenue|sales|guidance|outlook|q[1-4]\b|quarter|results|beats?|miss(es)?|raises?|lowers?`.

## Primary hypothesis and gate (development only)
H1: fades of `news_own` events lose money. Gate PASS when, on development events,
(a) `news_own` events n >= 100, (b) their mean net < 0 with a session-bootstrap (10,000 draws, seed 20261105)
one-sided 95% upper bound < 0, and (c) skipping them raises net per session vs take-all with a paired
session-bootstrap lower 95% bound > 0.
Held-out confirmation (run once, only after a dev PASS): `news_own` mean net < 0 and skip-news net per session
> take-all net per session. Both must hold for the live filter.

Secondary (reported, not gated): the same table for `news_any` and `news_earn`, and per-group win rates (used to
make the confidence number honest, below).

## What changes live
- PASS (dev + held-out): amendment F6: `news_own` events are not traded (paper and real); the text still goes out
  as "skipped: company news" with the headline. Forward check at n >= 300 as for F1.
- FAIL: the rule is unchanged. Headlines are still shown in every fade text as context, never as a gate.
- Either way the confidence number becomes the held-out win rate of the trade's news group with its 95% range,
  instead of one constant for every trade. It is a measured frequency, not a promise; it only spreads if the groups
  really differ.

Trial ledger run ids `fade-news-2026-11-05-dev`, `fade-news-2026-11-05-holdout`.

## Amendment A1 (2026-11-05, after the dev result, before the held-out run finished): what the confidence number means
Display only; changes no trade. Owner request: the number must move and say how sure we are. A per-trade win
chance cannot move much for this strategy (many small wins, few large losses: groups win 52-57%), so the headline
number becomes: confidence = round(100 x P(mean net of trades like this one > 0)), from a session bootstrap
(10,000 draws, seed 20261105) over ALL tested events (development + held-out) in the trade's news group
(`news_earn`, else `news_own`, else no company news). Shown next to it: that group's win rate, average net per trade
and n. Unknown news (feed down) = the all-events number. Recomputed only by re-running this study, never live.

## Result (2026-11-05, dev run once then held-out run once) — filter OFF, confidence by news group ON (A1)

| events | n | mean net | win |
|---|---|---|---|
| dev, no company news | 1,335 | +1.70% | 57% |
| dev, company news (`news_own`) | 1,010 | +0.02% | 52% |
| dev, earnings news | 211 | -0.27% | 53% |
| held-out, no company news | 504 | +1.53% | 58% |
| held-out, company news | 260 | +0.34% | 56% |
| held-out, earnings news | 55 | +1.72% | 51% |

Dev gate H1 FAIL: news fades break even rather than lose (upper bound +0.84% > 0), and dropping them does not raise
net per session (paired lower bound -2.3%), so the frozen rule keeps trading them. The split replicates on held-out
data never joined to news: the fade's edge is concentrated in alerts with no company news; with news it is about
zero. The earnings subset is noisy (sign flips between periods, n 55 held-out) and is not treated as its own signal.

Confidence (A1, all 3,109 events, `reports/fade_news/confidence.json`): no company news 100 (shown as 99; win 58%,
avg +1.65%, n 1,839), company news 55 (win 53%, +0.07%, n 1,004), earnings news 55 (win 52%, +0.14%, n 266), news
unknown = all events 100 (shown 99). Live texts show the number, the group, the latest headline and a chart link;
the news flags and the number shown are stored per trade (`fade_trades.news_own/news_earn/news_head/conf`) so the
split is re-checked on forward trades at n >= 300 alongside F1.

DNUT 2026-11-05 was an earnings-news fade (confidence 55 under A1).
