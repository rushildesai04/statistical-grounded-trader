# Pre-registration: a learned exit for the fade — cover when holding is predicted to lose (2026-11-06)

Committed BEFORE any feature or number in this file is computed.

## Why
Owner (2026-11-05/06): getting out at the right time is the most important part. Fixed exit rules (trailing stop,
VWAP reclaim, EMA 9/21 cross, 15% target; prereg_fade_exits_2026-11-05) and longer holds (prereg_fade_hold) all
earned less than the 60-minute hold, because each covers on a bounce that is usually noise. A learned exit differs:
it uses the whole state of the trade (how much we are up, how far it bounced from the low, RSI, VWAP, EMAs, volume,
time) and only covers when a model trained on earlier months predicts that holding to the champion's exit loses
money from here. If no such state is predictable, it covers rarely and matches the champion.

## Events, champion and checkpoints
Training events (`reports/telegram/trades_pooled_first.csv`, split train, status ok; 2025-01-03 .. 2026-06-30), the
champion's entry, 20% stop, 60-minute hold and costs (`sgt.fast.exits.simulate(CHAMPION)` = `alert_fade_test.score`).
Checkpoints: 10, 20, 30, 40 and 50 minutes after entry, at the close of the last RTH bar that ended by then, for
trades still open (not stopped) at that point. Covering at a checkpoint fills at the next bar's open (no look-ahead);
costs are identical either way (one round trip), so the comparison is on gross.

## Features at a checkpoint (only bars that ended by the checkpoint)
Gain so far (short: entry / close - 1), best gain so far, give-back from the best, minutes since entry, minute of
day, log price, alert type; tape signals from `sgt.fast.signals.snapshot` on the session's RTH bars: EMA 9/21 gap and
cross state, VWAP distance, RSI 14, MACD histogram, 5-minute relative volume, 1-minute ATR, distance from the high of
day, PVT trend, PSAR side.

## Label, model, policy
Label = (cover-now price - champion exit price) / entry, i.e. what holding to the champion's exit earns over covering
now (positive = hold). Model: `sgt.fast.meta.PARAMS` LightGBM regressor, label winsorized at the training 1st/99th
percentiles, monthly expanding walk-forward from 2025-07 (each month's events judged by a model trained only on
checkpoints of earlier months; >= 6 months). Policy X-LEARN: at each checkpoint in order, cover at the next bar's open
if the predicted label < 0; otherwise keep the champion's path. One policy (no threshold search).

## Gate (development, run once)
Total net per session, X-LEARN vs champion over the same walk-forward events: paired session bootstrap one-sided 95%
lower bound > 0 (10,000 resamples, seed 20261106) AND 1st-percentile per-trade net no worse than the champion's minus
5 points. PASS -> frozen model (`config/models/fade_exit_learn_v1.txt`) runs in shadow nightly (`fadeexitlearn` job);
recommended at forward n >= 300 only if its paired lower bound > 0 there. A live exit change needs the owner's OK
and an amendment. FAIL -> written up; the 60-minute exit stays.

## Secondary (reported only)
Share of trades covered early and at which checkpoint; mean gross of early covers vs what holding would have earned;
policy with a 0.5% margin (cover only if predicted label < -0.5%); held-out 2026-07..10 with the frozen model (already
seen, secondary); feature importance.
Trial ledger run id `fade-exit-learn-2026-11-06-dev`.
