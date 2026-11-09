# Measurement: real fills of the fade's 20% stop (2026-11-09)

Committed BEFORE any trade-level data is fetched. Trigger: adversarial review 2026-11-09 (red team F1): the scorer
fills every stop at max(stop level, bar open) on 1-minute bars; in the held-out 174 of 194 stops fill at exactly -20%.
+2% slippage per stop takes held-out from +1.12% to +0.67%; +5% to about 0. Real fills decide whether the edge exists.

## Method (all champion trades, training and held-out; a measurement, nothing is selected)
For each trade whose 60-minute window hits the stop (first RTH bar with high >= entry x 1.20): fetch SIP trades from
that bar's start for 60 s (up to 3 pages). Keep regular-way prints (drop conditions I, B, W, Z, 4, T, U, P, V, 7;
odd lots excluded; all venues kept: a stop-market buy takes whatever the market offers). Trigger = the first kept
print >= the stop level. Fill = the volume-weighted price of kept prints in [trigger, trigger + 2 s] (a broker stop
reaches the market within about a second), and never below the trigger price. Slippage = fill / level - 1.
Adjusted net = net - (fill - modelled cover) / entry. No trigger print found (data gap) -> modelled cover kept, counted.

## Reported
Slippage mean / median / p90; adjusted mean net per trade (training, held-out) vs published; by price band.
If adjusted held-out mean <= 0 -> the 20% stop as modelled is not a tradeable edge; the PLAYBOOK and the live book
say so, and a stop redesign is pre-registered separately.

## Result (2026-11-09)
Triggers found for 580 / 580 training and 193 / 194 held-out stops (SIP trades, 2-second VWAP after the trigger print).
| period | slippage mean / median / p90 | net per trade, modelled stops -> real stops |
|---|---|---|
| training | +0.82% / +0.31% / +2.13% | +0.97% -> **+0.80%** |
| held-out | +1.44% / +0.37% / +3.17% | +1.12% -> **+0.91%** |
By news group (real stops; session-bootstrap 5% lower bound):
| group | training | held-out |
|---|---|---|
| no-news | **+1.70%** [+0.88%] (n 1,238) | **+1.77%** [+0.47%] (n 487) |
| news | -0.20% [-1.00%] (n 1,107) | -0.60% [-2.32%] (n 277) |
With a 1.5% locate (vs the 0.5% assumed) no-news stays +0.70% / +0.77%; news falls to -1.2% / -1.6%.
Reading: the 20% stop slips about 1% on average (a few gap through by 2-3%+); the edge survives, but only in the
no-news group. News alerts are a losing trade once stops and borrow are real.
