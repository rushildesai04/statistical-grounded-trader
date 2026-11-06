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
