# Pre-registration: a regime gate for the alert fade (2026-11-13)

Status: FROZEN before any code or result. Script: `scripts/fade_regime_gate_study.py`, run ONCE.

## Source
Owner, 2026-11-13: "how much failing does it take for you and the algo to finally learn how to trade correctly in
different scenarios"; review 2026-11-13: the system has no sense of regime: the fade ran the same way in November 2026
(forward -3.44% per trade, win 39%) as in the months where alerted stocks reverted (finding 20). Question: could the
fade's own recent record have told it to stand aside, in the past as well as in November?

## Known (seen before this was written; stated so the test is read honestly)
Monthly training means: bad months were followed by good ones (2025-08 -2.54% then +0.78%; 2026-05 -1.46% then
+2.54%), so a gate may skip rebounds; losing streaks inside a day do not predict the next fade (finding 14). No
per-session gate statistic has been computed.

## Data
The frozen rule's per-trade nets (20% stop, 60 minutes, costs and locate), every alert event: training
`reports/fade_news/dev_events.csv` (2,345 fades, 354 sessions, 2025-01-03..2026-06-30, mean +0.97%), held-out
`reports/alert_fade/trades.csv` (764 fades, 2026-07-01..10-29), forward `reports/alert_fade/forward.csv`
(2026-10-30 on; descriptive only).

## Gate (decided before the open of session d, from earlier sessions only)
Look-back = the 5 most recent sessions before d that had at least one fade (training and held-out are one continuous
record for the look-back; the forward record continues it). If they hold fewer than 15 fades the gate is open.
- **G1**: stand aside on d when the look-back win share (net > 0) is below 50% (tested win rate 55-57%).
- **G2**: stand aside on d when the look-back mean net is below 0.

## Statistic and decision (alpha 0.05 / 2 = 0.025, one-sided)
Per trade of the period: gated net (0 if its session is skipped, else its net) minus ungated net; the statistic is the
mean of that difference = minus the summed net of the skipped trades over all trades. Session-clustered bootstrap
(10,000 resamples, seed 20261113); p = share of resampled statistics <= 0 (undefined counts against). Minimum: 100
skipped training trades.
ADOPT (as the paper rule that turns the fade off and on, with the owner's OK and section 0 of the PLAYBOOK) only if
the training statistic > 0 with p < 0.025 and the minimum met, AND the held-out statistic > 0 with >= 30 skipped trades
(held-out opened only for a gate that passes training). Else FAIL -> PLAYBOOK section 4.

## Secondary (reported, never decides)
1. Mean net and win share of kept vs skipped trades; share of sessions skipped.
2. Lag-1 correlation of session mean net (is there any persistence to exploit?).
3. November 2026 forward: the sessions each gate would have skipped and the gated vs ungated total.
4. Look-backs of 3 and 10 sessions (same thresholds), training only.

## Not done
No other thresholds, no intraday gate, no market-wide indicators (none is recorded point-in-time in this project).
