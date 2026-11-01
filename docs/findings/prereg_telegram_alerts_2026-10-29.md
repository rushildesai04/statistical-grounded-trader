# Pre-registration: "NEWS, VOLUME, BREAKOUTS" Telegram alert test (2026-10-29)

Committed BEFORE the channel history is downloaded and before any price is joined to an
alert. Nothing below may change after scoring. Changes made before scoring are allowed
only as dated amendments at the bottom, and only from message text, never from prices
or returns.

## Question
If an automated follower bought every alert of a given type as soon as it was posted,
would it make money after realistic costs? Long only (the trader: "don't short").

## Data
- Alerts: full history of the channel from 2024-01-01 (or channel start if later) to the
  download date, via `scripts/telegram_backfill.py`, parsed by `src/sgt/crowd/telegram_parser.py`.
  `posted_at` = Telegram server time (UTC).
- Prices: Alpaca SIP 1-minute bars (extended hours included) in `data/alpaca`, backfilled for
  every alert (symbol, session) pair with `scripts/backfill_gappers.py --lookback 0`.
- Excluded before scoring (counts reported): messages with `edited_at` set (an edit can carry
  later information); forwarded/quoted duplicates of an already-counted alert (same symbol,
  type, within 60 s); symbols the parser marks invalid.

## Frozen rule
- Alert types tested, each separately: `volume_increasing`, `gaining_volume`,
  `gaining_momentum`, `new_high`, `intraday_high`, `resumed`, `news` with `has_news = true`.
  `halted` is not tradable (the stock is halted); `after_hours_high` is outside the primary window.
  Plus one pooled cell: the FIRST alert of any type per (symbol, session).
- Unit: the FIRST alert of that type per (symbol, ET session date). Later repeats the same day are
  dropped (they overlap the first position).
- Entry: OPEN of the first 1-minute bar whose start is >= `posted_at` + 10 seconds (minimum
  10 s reaction time; an alert at 14:31:55 enters at the 14:33 open). No trade if that bar starts
  more than 5 minutes after `posted_at`.
- Exit (primary horizon H = 15 min): OPEN of the first bar whose start is >= entry time + 15 min.
  A halt therefore exits at the first print after it resumes. If no such bar exists in the same
  session, exit at the last bar's close of that session.
- Primary population: entry between 09:30 and 15:44 ET (horizon finishes inside regular hours),
  entry price $1-$50.
- Costs: round trip from `CostModelAdapter(GAPPER_COST_MODEL, open_hour_mult=1.38,
  rest_mult=0.81)` (`src/sgt/brokers/costs.py`, measured on 300 gap days) at the entry time and
  price. `net = (exit - entry) / entry - round_trip`.

## Split
Sort all eligible primary alerts (all types pooled, after dedup) by `posted_at`. D* = the ET
session date of the alert at the 75th percentile. Training = sessions before D*; held-out =
sessions on or after D*. Held-out is scored once, only for cells that pass training.

## Statistics
Day-clustered bootstrap (resample ET session dates with replacement), 10,000 resamples,
seed 20261029. k = number of cells with >= 100 training alerts. Training uses a one-sided
Bonferroni bound: the (1 - 0.05/k) one-sided lower bound of mean net.

## Decision criteria (per cell)
- PASS: training n >= 100, training lower bound > 0, top-5 trades < 50% of total training net
  profit; AND held-out n >= 30, held-out mean net > 0, top-5 < 50% of held-out net profit.
- INCONCLUSIVE: passes training but held-out n < 30 -> forward paper only, re-score when n >= 30.
- FAIL: otherwise.
- Overall: any PASS -> that cell (only) goes to a forward paper test, no capital. No PASS ->
  the follow-the-channel track stops.
- Data guard: if more than 20% of eligible alerts have no usable bars, the result is
  INCONCLUSIVE (data), not PASS or FAIL.

## Secondary (reported, never used for the decision)
- Horizons 5 and 60 minutes; gross (pre-cost) means; net at 1.5x costs.
- By session window (open / midday / power hour — the trader's claimed edge), by entry price
  bucket ($1-5, $5-20, >$20), news vs no news.
- Pre-market and after-hours alerts (`after_hours_high` included), gross and net with the
  open-hour cost multiplier.
- Latency: return from 15 minutes before the alert to entry (how much of the move was gone).
- Every scored cell is written to the trial ledger (`sgt.decision.trial_ledger`,
  run id `telegram-alerts-2026-10-29`).

## Amendments (before scoring only)

### A1 (2026-10-29, after download, before any price was loaded)
Source: the channel is MetaTradings' private "NEWS , VOLUME , BREAKOUTS" (id -1001254273411).
Downloaded 25,462 messages, 2024-01-04 .. 2026-10-30; 650 edited (excluded per the rule above).
The original grammar (built from the trader's video) parsed 40%. Templates were added from
message TEXT only (`src/sgt/crowd/telegram_parser.py`, tests
`tests/crowd/test_telegram_parser_2024_templates.py`): `$`/leading-dot prices; synonyms
("volume increase", "gaining volumed", "new day high", "new high of day", "day high");
"<phrase>, halted" -> `halted`; news blocks without a "-" line; one-line "TKR - headline" news;
new types `volume_spike`, `premarket_high`, `testing_high`, `price_level` ("over $N", "at N").
Result: 80% of messages parsed; the rest are banners, swing-trade recaps and templates with
< 40 occurrences each.

Cells, replacing the type list above (each still: first alert of that type per symbol-session):
`volume_increasing`, `volume_spike`, `gaining_volume`, `gaining_momentum`,
`high_of_day` (= `new_high` + `intraday_high`; the channel uses them interchangeably),
`testing_high`, `price_level`, `resumed`, `news` with `has_news = true`, plus the pooled
first-alert-of-any-type cell. `halted` stays excluded (not enterable). `premarket_high` and
`after_hours_high` are reported in the extended-hours secondary only. All other rules unchanged.
The second MetaTradings channel ("OPTIONS & STOCKS TRADE IDEAS") is NOT part of this test.

### A2 (2026-10-29, before any price was loaded)
Order of operations, fixed because the text above allowed two readings: the population
filter comes first, then "first alert of that type per symbol-session". Primary = the first
alert of the type whose entry falls in 09:30-15:44 ET (what a regular-hours follower would
take); the extended-hours secondary dedups separately. The pooled cell = first alert of any
cell type per symbol-session inside the primary window. D* uses the union of the per-type
primary trades. Secondary breakdowns are computed on TRAINING only, so held-out is looked at
solely for cells that pass training.

## Result (scored once, 2026-10-29) — VERDICT: FAIL
Data: 20,320 parsed alerts 2024-01-04..2026-10-29; 436 edited and 71 duplicates excluded; data guard
3.9% unusable (< 20%). D* = 2026-07-01. k = 7 cells with >= 100 training alerts, one-sided alpha 0.0071.

| cell (training, 15 min) | n | mean net | median | hit | lower bound | gross 15 min |
|---|---|---|---|---|---|---|
| pooled first alert | 2349 | -2.58% | -3.19% | 35% | -3.25% | -1.69% |
| high_of_day | 1166 | -1.76% | -2.79% | 36% | -2.72% | -0.90% |
| volume_increasing | 771 | -1.89% | -2.58% | 36% | -3.05% | -0.99% |
| news | 758 | -1.05% | -2.03% | 39% | -2.20% | -0.13% |
| resumed (after halt) | 484 | -3.86% | -6.41% | 33% | -5.92% | -3.06% |
| price_level | 279 | -1.11% | -2.06% | 43% | -2.95% | -0.34% |
| gaining_volume | 216 | -2.86% | -3.33% | 33% | -4.75% | -1.96% |

No cell passed training, so held-out was never examined. Every cell is negative BEFORE costs at 5, 15
and 60 minutes; the median stock had already risen 11-26% in the 15 minutes before the alert
("latency"), i.e. the alerts mark the top of a move that has already happened. Power hour (the
trader's claimed edge) was no better: -2.23% gross. Following this channel's alerts long is rejected.
Trial ledger: run id telegram-alerts-2026-10-29. Trade files: reports/telegram/.
