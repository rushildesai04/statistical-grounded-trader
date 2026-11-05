# Pre-registration: fade "brain" v2 — learn bet size from every signal (2026-11-05)

Committed BEFORE any v2 feature or number is computed.

## Why
Owner (2026-11-05): use all the day-trader signals and the news in the learning layer. Fade meta v1
(prereg_fade_meta_2026-11-04) learned to RANK alerts (rank IC +0.05; skipping the bottom half doubled the mean per
trade) but failed as a SKIP filter because the skipped alerts were still profitable. The right use of a ranking
whose every bucket is profitable is sizing: bet more on the better setups, less on the weaker ones, with the same
average size. Stated plainly: this sizing idea was formed after seeing v1's development result, so v2's development
gate is not independent evidence; the decisive test is forward.

## Events and label (no change to the trade)
Training events (`reports/telegram/trades_pooled_first.csv`, split train, 2025-01-03 .. 2026-06-30), label = the
champion's net (`alert_fade_test.score`), exactly as v1.

## Features (point in time; only information available before the alert)
- v1's 12 (`sgt.fast.meta.FEATURES`).
- 13 tape signals from `sgt.fast.signals.snapshot` at `posted_at` on the session's SIP 1-minute bars: EMA 9/21 gap,
  cross state, minutes since the cross, VWAP distance, RSI 14, MACD histogram, 5-minute relative volume, 1-minute ATR,
  distance from the high of day, above premarket high, PVT trend, PSAR side, minutes since the open.
- News: `news_own`, `news_earn` (prereg_fade_news events). SEC: `f_8k`, `f_dil` (prereg_fade_filings events; NaN
  where the ticker has no CIK).

## Model and validation
v1's LightGBM regressor and parameters (`sgt.fast.meta.PARAMS`), label winsorized at the training 1st/99th
percentile, monthly expanding walk-forward from 2025-07 (each month predicted by a model trained only on earlier
months, >= 6 months).

## Policy and gate (development)
Size weight from the prediction vs the terciles of that model's in-sample predictions: bottom third 0.5x, middle 1.0x,
top third 1.5x. Metric: sum over the session of weight x net, per session. PASS (goes forward as a shadow challenger)
when the weighted net per session beats equal weights with a session-bootstrap one-sided 95% lower bound of the
paired daily difference > 0 (10,000 resamples, seed 20261105), AND the mean weight over the walk-forward is within
0.9-1.1 (no hidden leverage).

## Forward (decisive)
The frozen model (`config/models/fade_brain_v2.txt`, sha256 in the trial ledger) sizes every forward event in shadow
from official SIP bars, news and filings. At forward n >= 300: RECOMMEND sizing by it only if the paired lower bound of
weighted vs equal net per session is > 0. Real sizing changes need the owner's OK and an amendment.

## Secondary (reported only)
Rank IC; mean net by tercile; the same sizing with v1's features only (does the new information add anything?);
feature importance; held-out 2026-07..10 with the frozen model (already seen, secondary).
Trial ledger run id `fade-brain-2026-11-05-dev`.
