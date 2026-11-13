# Pre-registration: break of structure (BoS) while EMA 9 > EMA 21 as a long setup on alerted stocks (2026-11-13)

Status: FROZEN before any code or result. Script: `scripts/bos_ema_study.py`, run ONCE.

## Source
Owner, 2026-11-12 night (XLAB chart with a "BOS & CHoCH" indicator): "When a BoS happens when 9 is above 21, that
pretty much means all sell orders have been cleared and there is nowhere to go but up. Every time BoS is printed, that
is followed by an explosive green candle the majority of the times." The owner's Pine source for that indicator did
not arrive, so this test uses the standard swing-pivot definition below (pivot length 5, the common default; it gives
about the label density on the owner's chart). If the owner's script differs materially it gets its own
pre-registration; this result still answers the stated claim for the standard definition.

## Known (not re-tested)
L6 "break of structure" long (our earlier definition, 30-minute watch, no EMA gate) FAILED: prereg_long_setups_2026-11-10.
The trader's five rules together picked worse longs (prereg_trader_rules_long_2026-11-12). New here: the standard pivot
BoS, the EMA 9 > 21 gate as the owner states it, the whole rest of the session, and the trend exit.

## Indicator (`sgt.fast.structure`, frozen at the study commit)
Per 1-minute bar of the symbol-session (all stored bars of the day, state from the first bar). Pivot high at bar j:
high_j strictly greater than the highs of the 5 bars before and >= the highs of the 5 bars after; it is CONFIRMED (usable)
from bar j+5. Pivot low symmetric on lows. State: lastHigh / lastLow = the most recent confirmed, not yet broken pivot
levels; trend in {none, up, down}. At bar i (using only bars <= i): close_i > lastHigh -> bullish break: it is a BoS if
trend is up, else a CHoCH; trend := up; lastHigh is consumed (no second signal on the same pivot). close_i < lastLow ->
bearish break: trend := down; lastLow consumed. If both happen on one bar the bearish one is applied first (conservative).

## Population, entry, costs, bootstrap
Exactly as prereg_bull_flag_finder_2026-11-13: the long book's events and split, signals from the event's t0 with the
signal bar closing no later than 15:30 ET, gate EMA9 > EMA21 at the signal bar's close, buy at the next bar's OPEN, one
position per symbol at a time, net = gross - the event's cost_rt, session-clustered bootstrap (10,000, seed 20261113),
p = share of resampled means <= 0.
Stop: the most recent confirmed pivot low at the signal bar (unbroken or not); skip the trade if there is none or the
entry open <= that level. Exits X1 (2R target, else the close 30 minutes after entry) and X2 (sell at the next open
after a close with EMA9 < EMA21, at the latest the 15:55 close), stop checked first in every bar, as in the bull flag
pre-registration.

## Candidates (alpha 0.05 / 2 = 0.025, one-sided)
S1X1, S1X2: every gated bullish BoS. Minimum 100 training trades.

## Decision
ADOPT as a paper long-book setup (owner's OK) only if training mean net > 0 with p < 0.025 and the minimum met, AND
held-out mean > 0 with n >= 30 (opened only for a candidate that passes training). Else FAIL -> PLAYBOOK section 4.

## Secondary (reported, never decides)
1. The owner's claim itself: after a gated BoS bar, share of next bars that close green, share with a next-bar return
   >= +1% ("explosive"), and the mean next-bar return, against all gated bars of the same events (session bootstrap of
   the difference).
2. Gated bullish CHoCH (same rules) and BoS without the EMA gate.
3. November 2026 forward events (2026-10-30..11-12), descriptive.
4. Exit mix and the mean of winners and losers per candidate.

## Not done
No other pivot lengths, no wick-based breaks, no higher-timeframe structure.

## Result (run once, 2026-11-12 night; `reports/bos_ema/result.json`) - both FAIL on training
| candidate | training n | mean net | median | win | p | exits |
|---|---|---|---|---|---|---|
| S1X1 gated BoS, 2R / 30 min | 5,017 | **-1.45%** | -2.31% | 33% | 1.00 | time 61%, stop 28%, target 11% |
| S1X2 gated BoS, hold while 9 > 21 | 4,531 | **-1.56%** | -2.85% | 23% | 1.00 | trend 82%, stop 15% |

Held-out stays closed. The owner's claim, measured (training, 6,648 gated BoS bars vs 299,404 gated bars): next bar
green 44.7% vs 43.6% (p 0.046), next bar up 1% or more 21.4% vs 17.3% (p < 0.001), mean next-bar return +0.08% vs
-0.01% (p 0.001). So a BoS with 9 > 21 IS followed by a strong candle more often than usual, but not "the majority
of the times", and the lift is a tenth of a percent against a ~1.25% round-trip cost. Secondary: gated CHoCH -0.83% /
-1.04%; BoS without the EMA gate -1.45% / -1.54% (the gate adds nothing). November 2026 (66 forward events,
descriptive): S1X1 -0.13% (n 136), S1X2 -0.04% (n 119): about break-even, the best month on record for it, still not
positive. XLAB 11-12: X1 +8.1%, +5.8%, +22.7%, -9.8%; X2 one trade $1.44 -> $2.28, **+57%**.

## Addendum A1 (2026-11-13, FROZEN before the run): the owner's own "BOS & CHoCH" Pine source
The owner pasted the script (Pine v6, pivot length 5). It matches the standard definition above except: (1) the first
bullish break of the day, before any trend exists, is labelled BOS (above: CHoCH); (2) on a bar that breaks both
levels the bullish break is applied first, then the bearish one. `structure.bullish_breaks(owner=True)` implements
exactly that. Candidates O1X1, O1X2 = S1X1, S1X2 with this labelling; same rules, alpha, minimum, decision and
bootstrap; run once (`scripts/bos_ema_study.py --owner`, output `reports/bos_ema/result_owner.json`). Expectation
stated before the run: the owner's BOS set is the S1 set plus part of the CHoCH set, and both lost on training
(-1.45% / -0.83% with X1), so a pass would be a surprise.
