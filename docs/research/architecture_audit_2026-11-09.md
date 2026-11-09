# Architecture audit: how to make the trades smarter (2026-11-09)

Owner: "a massive audit of the entire architecture ... more intelligent and logic-backed trades ... rather than losing
most of the time for no reason." Three independent reviews (architecture, quant, learning loop; all read-only) plus my
own checks. Earlier the same day: red-team / code / design reviews of the fade (`prereg_fade_halt_audit`,
`prereg_fade_stop_slippage`, `prereg_long_family`).

## 1. Verdict
The system is sound and auditable: no look-ahead or leakage in the backtests, live trades are re-scored on SIP by the
research code, every job is idempotent. What limits money is not bugs but four things:
1. **The alert fade decides on one gate at one size.** It knows news, time of day, price band, pre-alert volatility,
   borrow, social buzz and chart signals, but only news (F6) changes a decision; everything else is displayed.
2. **Live and research compute features separately.** Every live/backtest divergence found so far (RAYA, OMH, FISN)
   came from this; parity is asserted by tests, not guaranteed by one code path.
3. **The learning loop measured but did not react**: drift monitors existed but were unwired (fixed today), AI lessons
   had no queue (fixed), group statistics are never re-estimated from live data.
4. **Data**: IEX-only real time (thin names print late or not at all), one alert source.

## 2. "Losing most of the time": the honest numbers (training, 2,345 fades)
| horizon | negative share | note |
|---|---|---|
| per trade | 45% lose (55% win), median trade +1.67% | stop-outs (-20%) make the mean (+0.97%) smaller than the median |
| per day | 45.5% of 354 days red | mean day +0.91% |
| per week | 41% of 76 weeks red | |
| any 2-week window | **36% red even with the edge intact** (5-95% band of the mean: -3.3% .. +4.6% per trade) | two red weeks cannot tell "broken" from "normal" |
Live losses so far were dominated by execution divergence (halts, stale IEX prints), now fixed, not by a decayed edge.
Judge each book at n >= 300 (the live monitor shows it against its tested range every night).

## 3. Decision path today (who actually decides)
| book | decides with | sizes with | known but unused |
|---|---|---|---|
| alert fade | alert cell, 09:30-15:44, $1-50, first event, **no-news (F6)**, tested entry bar | flat 5% NAV | time of day, price band, volatility quintile, borrow, social, signals |
| scanner (experiment) | tested spike rule | flat 0.5% | everything |
| megacap / megacap_ext | per-ticker LightGBM | ATR/beta risk sizing | - |
| overlay / ETF / turn-of-month | fixed rules | fixed weights | - |
Each book is its own ledger: no shared exposure, no portfolio limit (the risk engine in `sgt/risk/manager.py` serves only
the older `sgt/live/runner.py`, which no job runs).

## 4. Ranked roadmap
| # | change | why (evidence) | effort | how it is validated |
|---|---|---|---|---|
| 1 | **Expected-value scorer + EV sizing for the fade** (one table: news x time-of-day x price band x volatility quintile, shrunk toward the pooled mean; size = clip(EV / typical EV)) | findings 1, 2, 12, 15: each group differs by 1-4 points per trade; flat sizing ignores it | 1-2 days | forward shadow on every live alert (scoreboard, n >= 300); training/held-out are descriptive for most of these |
| 2 | **Shared point-in-time feature module** used by research AND live (bars strictly before the decision time) | every divergence so far; today's "exhaustion" lead was a look-ahead artifact (below) | 2-3 days | parity test: research and live produce identical features on replayed days |
| 3 | ~~Paid real-time SIP feed~~ (DROPPED by the owner 2026-11-09: no spending until a book is proven; work with free IEX) | FISN stale price, IEX had bars for 26 of 32 scanner names, stop checks lag on thin names | config + 1 day | live-vs-official gap per trade (realism) should shrink |
| 4 | **Portfolio layer**: one NAV view across books, per-name net exposure, wire `sgt/risk/manager` as the single pre-trade gate | megacap + megacap_ext + overlay can stack the same names; required before real money | 2-3 days | unit tests + paper replay |
| 5 | **Weekly re-estimation of group stats** from live + forward data (with shrinkage) feeding the EV table and the "win chance" text | today's tables are frozen training numbers | 1 day | compare predicted vs realized by group monthly |
| 6 | **Second alert source** (the fade's edge is one channel's selection, finding 11) | concentration risk | research, open | pre-registered like the channel |
| 7 | Learning loop leftovers: F6 rule as a scoreboard row on the forward record; digest scoreboard line is a day old (label) | audit F2/F5 | hours | - |

**Will not help** (all tested and rejected, PLAYBOOK section 4): chart-signal exits (trailing, VWAP, EMA, targets,
partial profits), waiting for a breakdown, daily loss breakers, longs on the same alerts, inverse-volatility sizing,
risk-off skips, limit-order exits. Do not rebuild these under a new name.

## 5. Today's quant lead that did NOT survive
The quant review found that fades entered on a fresh session high lose (-0.59%) while the rest earn +1.99% (training;
held-out -1.29% vs +2.69%). It used the entry bar's own high, which happens AFTER the entry (the trade fills at that bar's
open): look-ahead. With point-in-time data only, the split disappears or reverses (last completed bar at the high:
+1.15% vs +0.60% training, +0.70% vs +2.06% held-out; entry price at the prior high: +0.64% vs +0.99%, +0.57% vs +1.20%).
Not adopted. This is exactly what roadmap item 2 prevents.

## 6. Done today (from the audits)
Live entry = tested entry bar (halts, stale prints); no-news rule (F6); breaker removed; real stop fills measured;
longs tested (fail); parallel context lookups; Models tab on the phone; tabs fixed; Notes tab + AI analyst; live drift
monitor per book; AI test queue; scanner experiment; model books auto-execute; daily models extended to 21 names
(history download running on the runner).
