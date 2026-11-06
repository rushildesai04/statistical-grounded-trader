# Pre-registration: fade the channel's LATER alerts on the same stock (2026-11-06)

Committed BEFORE any later-alert event is built or scored.

## Why
The live fade trades only the first alert per symbol-day (`pooled_first`). The channel often alerts the same stock
again later. The self-fade study (2026-11-06) showed the edge is the channel's selection, so more of its selections
may carry the same edge: more trades from the proven source.

## Events
Same alerts, cells, entry rule and filters as `pooled_first` (`telegram_alert_test.primary_events`: cell alerts, entry
inside 09:30-15:44 ET, $1-50, status ok). Later alert = any primary event that is NOT the first of its symbol-day and
whose entry is at least 60 minutes after the entry of the previous KEPT event of that symbol-day (the first alert is
always kept; trades never overlap). Trade, stop, hold and costs: `alert_fade_test.score` (20% stop, 60 min, gapper
cost + 0.5% locate), identical to the live fade.

## Split and gates
Training: sessions < 2026-07-01 (the champion's training range); held-out: 2026-07-01 .. 2026-10-29 (the champion's
held-out period; later alerts there have never been scored).
Training (run once): PASS needs >= 200 events, mean net > 0 with a day-cluster bootstrap one-sided 95% lower bound > 0
(10,000, seed 20261106), both halves positive (split 2025-10-01), no month > 50% of the total.
PASS -> held-out once, same criteria with >= 50 events. Held-out PASS -> later alerts join the live book as a shadow
first (forward n >= 100 later-alert trades with a lower bound > 0) before they are traded; owner OK needed.

## Secondary
Mean by alert number (2nd, 3rd+), by minutes since the first alert, news / no-news, by entry window; the first-alert
champion on the same sessions for comparison.
Trial ledger run ids `fade-later-2026-11-06-dev`, `fade-later-2026-11-06-holdout`.

## Result (2026-11-06, training run once, trial ledger `fade-later-2026-11-06-dev`) — FAIL; held-out not scored
(A first run crashed in the shared scorer on a date type before any event was scored; fixed and re-run.)
402 later alerts (non-overlapping repeats), training: mean net -3.19%/trade (gross -2.09%, lower 95% -4.33%), halves
-3.40% / -3.07%; 2nd alert -3.44% (n 340), 3rd+ -1.79% (n 62); before 11 / 11-14 / after 14: -3.54% / -2.71% / -4.16%.
Reading: a stock the channel alerts again an hour later is in a sustained run; shorting it loses. The live book's
first-alert-only rule is right. Follow-up hypotheses formed from this result get their own preregistration.

## Addendum A (2026-11-06, written before computing): exit when the channel re-alerts during the hold (X-REALERT)
On the champion's training trades (first alert per symbol-day), if any later primary alert of the same symbol-day is
posted after the entry and before the champion's exit, cover at the open of the first bar at or after that alert's
time + 10 s (the live reaction), unless the 20% stop was hit first. Otherwise the champion's exit. Same gate as the exit
studies (paired per-session vs champion, session bootstrap 10,000, seed 20261106, one-sided lower bound > 0 at alpha
0.05, 1st percentile no worse than champion - 5 pts); champion guard; held-out secondary. Secondary: share of trades
re-alerted during the hold, their net held vs covered.

## Addendum A result (2026-11-06, run once, trial ledger `fade-realert-2026-11-06-dev`) — FAIL
Champion guard passed. X-REALERT +0.56%/trade vs champion +0.97% (train; diff/session -2.73%, lower 95% -4.68%);
held-out (secondary) +0.75% vs +1.12%. A third of trades are re-alerted during the hold (32% / 37%); those lose
-4.6% / -4.7% if held, and -5.9% / -5.7% if covered at the re-alert. Reading: the channel re-alerts on new highs, i.e.
at the top of the squeeze; covering there buys back at the worst price before the fade resumes.
