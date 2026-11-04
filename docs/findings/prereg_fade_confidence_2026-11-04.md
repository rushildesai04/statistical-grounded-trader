# Pre-registration: a calibrated confidence score for every fade trade (2026-11-04)

Committed BEFORE the classifier, calibrator or any score below is computed.

## Owner request (2026-11-04)
Every trade carries a confidence score: 100 = very likely to make money, 0 = very likely to lose. Never take a
trade below 30. When an approval is about to expire unanswered, auto-approve it if the score is >= 70. Built
like a decision model that outputs calibrated probabilities over a fixed answer space (e.g. TypeSafe's Jev):
answer space {take, skip}, the score is a probability that has to be calibrated, judged by Brier score.

## Definition
score = round(100 x P(net > 0 | features at the alert)), net = the fade's net return (gapper cost + 0.5%
locate). Features: `sgt.fast.meta.FEATURES` (point in time). Also shown: expected net (monotone isotonic map
from the raw score to net, fitted on past out-of-sample trades) in % and in dollars.

## Model (frozen)
LightGBM classifier on net > 0 with `sgt.fast.meta.PARAMS`. Calibration: isotonic regression from raw score to
outcome, fitted ONLY on earlier out-of-sample predictions (nested walk-forward): month m is scored by a model
trained on months < m and a calibrator fitted on the out-of-sample raw scores and outcomes of months < m.
Evaluation months: those with >= 3 months of earlier out-of-sample predictions (training events, from 2025-07
for raw scores, so calibrated scores from 2025-10 .. 2026-06).

## Gates (development data only; each switches one live behaviour on or leaves it off)
- G1 calibration (score shown in texts): expected calibration error (10 equal-width bins) <= 0.05 AND Brier
  skill vs the base rate > 0. If G1 fails, no score is shown and G2/G3 are off.
- G2 floor ("never below 30"): trades scored < 30 must have mean net < 0 with a session-bootstrap one-sided
  95% upper bound < 0, n >= 50. Otherwise the floor is OFF, because skipping trades with a non-negative
  expectation costs money (the v1 filter showed the fade's edge is broad).
- G3 auto-approve ("about to expire and >= 70"): trades scored >= 70 must have mean net > 0, n >= 30.
  Auto-approval only ever ADDS trades the tested rule takes anyway, so it cannot move the book away from
  the tested rule; G3 guards against the score being anti-informative at the top.
Reported regardless: score distribution (share < 30, 30-70, >= 70), reliability table, Brier, win rate and mean
net per band.

## Forward
The frozen model scores every live event; the score, expected net and the decision (asked / auto-approved /
floored) are stored per trade. Forward calibration is re-checked at n >= 300 (same G1 test); a failure turns
the score off. Trial ledger run ids `fade-confidence-2026-11-04-dev` and `fade-confidence-v1`.
