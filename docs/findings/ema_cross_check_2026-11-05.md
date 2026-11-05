# EMA 9/21 cross: does it predict the next move? (fidelity check, 2026-11-05)

Why: the owner asked why the strategy ignores the trader's 9/21 EMA. The adversarial review (2026-11-05) found two
fidelity gaps in the original test of the trader's rules: it used EMA 9/24 although the trader said "9/21"
(F-1, `src/sgt/decision/trader_rules.py:47`), and its 3-bar-low stop ended most trades within ~2 minutes, so the
entry signal was never measured on its own (F-2). This check closes both. It is a diagnostic of a family already
rejected, not a strategy test; anything positive here would need its own pre-registration and a fresh test.

Method (`scripts/ema_cross_check.py`): gapper universe (`data/universe/gappers_2y.csv`, prior-day volume >= 50k),
sessions before 2026-04-01 (375 sessions), RTH 1-minute SIP bars. Signal at a bar's close, enter at the next bar's
open, mark at the close h minutes later; no stop, no exit rule. Net = gross minus the measured gapper round trip
(`GAPPER_COST_MODEL`). 95% intervals: session bootstrap (2,000 resamples, seed 20261105).

| signal | n | cost | 5 min gross | 15 min gross [95%] | 30 min gross [95%] | 60 min gross [95%] | best net |
|---|---|---|---|---|---|---|---|
| EMA 9 crosses above 21 | 46,711 | 0.69% | -0.01% | -0.01% [-0.05, +0.03] | +0.01% [-0.06, +0.07] | -0.02% [-0.13, +0.09] | -0.68% |
| EMA 9 crosses above 24 (old test) | 43,403 | 0.69% | -0.02% | -0.02% [-0.06, +0.02] | -0.01% [-0.07, +0.06] | -0.05% [-0.16, +0.06] | -0.70% |
| 9/21 cross up + volume >= 3x | 6,708 | 0.74% | -0.05% | +0.02% [-0.12, +0.16] | +0.08% [-0.13, +0.31] | +0.05% [-0.16, +0.27] | -0.66% |
| 9/21 cross down, short side | 47,690 | 0.70% | -0.05% | -0.03% [-0.07, +0.00] | -0.02% [-0.08, +0.05] | -0.02% [-0.13, +0.10] | -0.72% |
| trader's full entry on 9/21 (cross + prior gap + volume spike + PVT + MACD + RSI), all day | 2,256 | 0.81% | -0.09% | +0.12% [-0.17, +0.44] | +0.34% [-0.12, +0.90] | +0.25% [-0.21, +0.76] | -0.46% |
| trader's full entry on 9/21, power hour only | 269 | 0.70% | -0.01% | +0.06% [-0.43, +0.53] | +0.14% [-0.44, +0.75] | +0.14% [-0.66, +0.91] | -0.56% |

Reading: 9/21 versus 9/24 makes no difference; a cross in either direction is followed by a move indistinguishable
from zero (within +/-0.05%) at every horizon, against a 0.7% cost to trade these stocks. The trader's full setup on
9/21 is the only cell with a positive point estimate (+0.34% at 30 minutes), its interval includes zero, and it is
still about half a percent short of covering costs. Win rates after costs are 25-41%. F-1 and F-2 are closed: the
earlier verdict on the trader's rules stands, and neither the span nor the exit policy was hiding an edge.
