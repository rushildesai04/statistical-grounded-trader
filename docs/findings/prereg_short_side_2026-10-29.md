# Pre-registration: short-side lockbox test (2026-10-29)

Committed BEFORE the lockbox is scored. Nothing below may change after the run.

## Origin (exploratory, training period only)
Walk-forward inside 2023-01..2025-09 on `data/decision/gappers_5y`: shorting the decision
model's bottom-decile predicted return on in-play gap days, stocks >= $5, one short per
stock-day: n=684, mean +0.54%/trade net of costs, 90% day-clustered CI [+0.11%, +0.95%].
The $5 cut was chosen after looking -> this test exists to confirm or reject it.

## Frozen rule
- Data: `data/decision/gappers_5y/dataset.parquet` (gap days, prev-day volume >= 50k, 6 intraday
  triggers, candidates from the regular open onward, gapper time-of-day costs).
- Model: `DecisionModel().fit` on ALL rows with `session_date < 2025-10-01`, capped to 10 rows per
  (symbol, session) by the seeded shuffle (seed 0) used in training; label
  `y_net__tp2_sl2_h15`, weights `uniq_w__tp2_sl2_h15`, features = every `f_*` column, seed 0.
- Threshold: 10th percentile of the model's predicted `mu` on its own training rows.
- Lockbox rows: sessions 2025-10-01 .. 2026-10-16, UNCAPPED (what a live system would see).
- Entry: a candidate with `mu <= threshold` and `exp(f_log_price) >= 5`; the FIRST such candidate
  per (symbol, session) by time. Short at the label's entry; symmetric 2-ATR bracket, 15-minute
  horizon (short outcome = mirror of the long label).
- Net per trade: `short_net = -(y_net + cost_rt_label) - cost_rt_label` (spread paid both ways).

## Decision criteria
- PASS: n >= 50, mean short_net > 0, and the 90% day-clustered bootstrap CI lower bound > 0.
- INCONCLUSIVE: n >= 50 and mean > 0 but CI includes 0 -> paper trading only, no capital.
- FAIL: otherwise -> short side rejected; the day-trading track stops.

## Secondary (reported, not used for the decision)
- Same rule on rows capped like training (consistency with the exploratory result).
- Current Alpaca `shortable` / `easy_to_borrow` flags of traded symbols (today's flags, not
  historical) and the result on the easy-to-borrow subset.

## Result (scored once, 2026-10-29) — VERDICT: FAIL
- PRIMARY (uncapped, first qualifying candidate per stock-day): n=1107, mean -0.320%/trade,
  median +0.300%, hit 51%, 90% CI [-0.583%, -0.052%], worst -30.18% (squeeze), 254 days.
- Secondary (capped like training): n=585, mean +0.030%, CI [-0.349%, +0.428%].
- Easy-to-borrow today (39% of names): n=322, mean -0.796%, CI [-1.178%, -0.435%].
- The exploratory +0.54% depended on the random per-day sampling of candidates, not the rule.
Short side rejected. Combined with the long-side results (gappers_5y: 0 OOS trades; best
decile -0.46..-0.59%/trade), the intraday day-trading track stops per the criteria above.
