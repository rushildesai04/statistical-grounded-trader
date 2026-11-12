# Pre-registration: breakeven stop after +1R on the A2 long book (owner's RETO long note, 2026-11-12)

Status: FROZEN before any run. Script: `scripts/long_risk_study.py --breakeven`, run ONCE.

## Source
RETO long 2026-11-12 (owner note): "It briefly reached about $10.20, then reversed, turning roughly 9.3% open profit
into a loss ... Protecting profit after a failed breakout is an idea to test". Luna (long:18): "This exact long-book
rule was not tested. It needs a pre-registered trigger ... and fixed execution rules."

## Known (not re-tested)
Runner exits (half at 2R + trail; whole runner) FAIL (prereg_long_runner_2026-11-11); MACD hold FAIL. The live long
book now runs A2 (prereg_long_risk_2026-11-12: risk size w = min(1, 2.68% / d) and the red-fill-candle exit E):
training per dollar -1.88%, held-out -1.91%.

## Population
Identical to prereg_long_risk_2026-11-12 (same events, BASE fills, costs, split; first_fill, patterns.simulate).

## Candidate (one; alpha 0.05 one-sided)
**BE**: the A2 trade (E exit on the fill bar), plus: once a CLOSED bar at or after the fill bar has a high >= entry +
1R (R = entry - stop), the stop moves to the entry price from the NEXT bar on (the order inside the arming bar is
unknown, so it is never used). Each later bar: original stop or breakeven stop first (low <= stop level: sell at
min(stop level, open)), then the 2R target (high >= target: sell at max(target, open)), else the 30-minute time exit
at the last bar's close, as the live simulator. Statistic: per dollar (A2 weights) mean net of BE minus per dollar
mean net of A2 (paired per trade, sessions resampled). Minimum: 100 training trades changed by the rule.

## Decision
Session-clustered bootstrap (10,000 resamples, seed 20261112), one-sided p = share of resampled statistics <= 0.
ADOPT (paper long book, owner's OK) only if training statistic > 0 with p < 0.05, minimum met, AND held-out statistic
> 0 (held-out opened only on a training pass; n >= 30 changed). PROFITABLE as in prereg_long_risk. Else FAIL
(PLAYBOOK section 4). Secondary (never decides): share of trades armed; mean of the changed trades under A2 and BE.

## Not done
No other trigger levels (+0.5R, +1.5R) or trails: +1R and the entry price are fixed here.
