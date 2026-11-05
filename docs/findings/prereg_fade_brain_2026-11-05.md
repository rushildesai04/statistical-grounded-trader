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

## Result (2026-11-05, dev walk-forward run once, trial ledger `fade-brain-2026-11-05-dev`) — PASS (development)
(A first `dev` run crashed while loading events, on a duplicate column name, before any feature or number was
computed; fixed and re-run.) Walk-forward 2025-07 .. 2026-06, 1,658 events, 234 sessions.

| | IC | net per session, equal | sized | difference [lower 95%] | mean size | 0.5x / 1.0x / 1.5x mean per trade |
|---|---|---|---|---|---|---|
| v2 (all features) | +0.045 | +6.54% | +8.90% | +2.37% [+0.46%] | 0.97 | +0.17% / +0.37% / +2.52% |
| v1 features only (secondary) | +0.051 | +6.54% | +9.05% | +2.52% [+0.46%] | 0.98 | +0.29% / +0.04% / +2.75% |

Frozen model (`config/models/fade_brain_v2.txt`, sha256 56015d13cefc...), secondary held-out 2026-07..10 (n 764,
85 sessions; examined before for other studies, never by this model): IC +0.141; net per session equal +10.10%,
sized +18.40%, difference +8.31% [lower 95% +4.26%]; mean size 1.02; 0.5x -1.33% / 1.0x +0.37% / 1.5x +4.03%.

Reading: sizing by the model adds about a third more profit per session on training and more on the held-out
period, where the bottom third lost money. The tape signals, news and SEC flags did not add to v1's simple price
features (equal results); the gain comes from sizing, not from the new inputs. Top features: 1-minute ATR, price,
minutes since the EMA cross, recent returns, EMA gap. Not independent evidence (the sizing idea came from v1's dev),
so the decision is forward: `fadebrain` shadow job nightly; recommendation only at forward n >= 300 with a paired
lower bound > 0. First local forward look (29 events, too few): mean size 1.19x, watched for drift.
