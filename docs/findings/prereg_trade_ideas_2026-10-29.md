# Pre-registration: MetaTradings "OPTIONS & STOCKS TRADE IDEAS" stock calls (2026-10-29)

Committed BEFORE any price is joined to these calls. Nothing below may change after scoring.

## Question
If the live bot (`src/sgt/signals_live`, commit 4c0f145) had traded every stock call in this channel
exactly as it trades them now, would it have made money after costs?

## Data
- Calls: channel id -1001204455879, 2024-01-01 .. 2026-10-29, parsed by
  `sgt.signals_live.ideas.parse_idea` (1,344 stock calls; option calls excluded). Edited messages
  excluded. `posted_at` = Telegram server time.
- Prices: Alpaca SIP 1-minute bars (`data/alpaca`), backfilled for every call's (symbol, session).

## Frozen rule (identical to the live bot's `rules.decide_idea`, default `Limits`)
- Eligible: posted 04:00-15:30 ET on a trading day, ideal entry $1-50, one call per symbol per day
  (the first). Skip if the last trade before posting is > entry x 1.03 ("already ran").
- Entry: buy limit = round(entry x 1.005), regular hours only, valid from max(posted, 09:30 ET)
  until posted + 10 min (a call posted 09:28 is live 09:30-09:38). Filled in the first bar whose
  low <= limit, at min(limit, that bar's open). No fill -> no trade.
- Size $2,000 split into one leg per PT (usually 3): take-profit at entry x (1 + PT_i) (not below
  limit + 0.01), shared stop at SL. From the bar AFTER the fill: stop if low <= stop, filled at
  min(stop, open); take-profit if high >= TP, filled at TP. A bar touching both counts as the stop
  (conservative). Remaining shares exit at the 15:55 ET bar's open.
- Costs: round trip from `CostModelAdapter(GAPPER_COST_MODEL, 1.38, 0.81)` at the fill time and
  price, charged once per trade on the whole position (`net = pnl / notional - cost`).
- Unit = one call; return = position P&L / filled notional, net.

## Split and statistics
Chronological: D* = ET session date of the 75th-percentile filled call. Training < D*, held-out >= D*.
Day-clustered bootstrap, 10,000 resamples, seed 20261029. One primary cell (all calls), one-sided 95%.

## Decision
- PASS: training n >= 100, lower bound of mean net > 0, top-5 trades < 50% of total net profit; AND
  held-out n >= 30, mean net > 0, top-5 < 50%. -> the live bot keeps trading these on paper and they
  become the candidate for real money after >= 3 months of matching paper results.
- INCONCLUSIVE: training passes, held-out n < 30.
- FAIL: otherwise -> stock calls go watch-only like the news/volume alerts.
- Held-out is examined only if training passes. Data guard: > 20% of eligible calls without bars
  -> INCONCLUSIVE (data).

## Secondary (training only, never decides)
Fill rate; share of filled calls whose high reached +5% before the stop (their "went green" claim);
win rate; by "HIGH RISK" flag; by time of day; gross vs net; results with entry at the posted price
with no limit (market) for comparison. Trial ledger run id `trade-ideas-2026-10-29`.

## Result (scored once, 2026-10-29) — VERDICT: FAIL
1,326 calls (first per symbol-day): 1,088 filled, 75 not filled in 10 min, 163 skipped (140 "already
ran" > 3% above entry, 23 under $1). Data guard 0.0%. D* = 2026-03-26.

| training (2024-01 .. 2026-03) | value |
|---|---|
| filled calls | 814 (fill rate 93%) |
| mean net per trade | **-1.27%** (median -1.02%), 95% one-sided lower bound -1.90% |
| gross before costs | -0.10% |
| measured cost (round trip) | 1.16% |
| win rate | 47% |
| reached +5% before the stop | 74% |
| stopped out | 47%; stopped trades average -9.8% gross, the rest +8.6% |
| net if costs were only half | -0.68% |
| market entry instead of limit | -1.51% |
| HIGH RISK / normal | -1.20% / -1.28% |

Training failed, so held-out (274 calls) was never examined. Reading: the calls often do go green
(74% touch +5%, consistent with the channel's "went green" claim), but the wide stop (median 16%
below entry) loses about as much as the targets win, so the edge before costs is ~0, and
low-priced stocks in the first minutes after the open cost ~1.2% per round trip.
Per the rule above, stock calls go watch-only in the live bot.

Correction (same day): the first printout showed "reached +5% before stop 0%". That was a display
bug (numpy booleans in an object column summed with logical OR); fixed in
`scripts/trade_ideas_test.py`, value recomputed from `reports/trade_ideas/trades.csv` (74%). The
primary result uses float returns and was not affected; nothing was re-scored.
Trial ledger: run id trade-ideas-2026-10-29.
