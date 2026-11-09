# Pre-registration: skip calm fades / size up jumpy ones — forward only (2026-11-08)

Committed BEFORE any forward trade is scored with it. Source: `prereg_fade_volsize_2026-11-08` (result section). The
effect was found by looking at training and held-out data, so neither can test it; only live trades can.

## Measure (point in time, SIP bars, scored nightly after the session)
sigma = sd of 1-minute log close-to-close returns over the last 30 bars that ended at or before the entry (>= 10
bars, else unknown -> treated as the live rule, weight 1). Constants fixed from training (2025-01-03 .. 2026-06-30):
CALM = 0.015072 (20th percentile), M = 0.025540 (median).

## Challengers (shadow; the live rule is unchanged)
1. **calm skip** (primary): weight 0 when sigma < CALM, else 1.
2. **vol up** (secondary): weight clip(sigma / M, 0.5, 2.0).
Scored by the learning scoreboard (`scripts/learning_report.py`, kind "size"): paired per-session difference vs the
live rule, session bootstrap, alpha 0.05 (calm skip) / 0.05 (vol up, reported apart). Decision at forward n >= 300:
READY + lower bound > 0 -> proposed to the owner; live change only with the owner's OK and an amendment.

## Expected if real
Training: skipping the calmest fifth removes trades averaging -1.3% -> per-trade about +1.5% instead of +0.97%, total
profit up about +0.6% per session. Forward will be noisier.
