# Pre-registration: the owner's "Clean Bull Flag Finder" as a long setup on alerted stocks (2026-11-13)

Status: FROZEN before any code or result. Script: `scripts/bull_flag_finder_study.py`, run ONCE.

## Source
Owner, 2026-11-12 night, XLAB (alert 11:43 ET at $1.28; the fade shorted $1.29 and was stopped; the stock reached $2.50):
"there were even 2 separate bull flags (BF%) with strength of over 75% ... during this whole time 9 stayed above 21 ...
go build this into the algorithm in some way that's optimal". The owner supplied the TradingView Pine v5 source of
"Clean Bull Flag Finder (Box Style + Strength)" (default inputs) used on that chart. Owner, same night: shorting is
deprioritized; the long side is the focus.

## Known (not re-tested)
Our own bull flag definitions failed as longs on this channel's alerts: L5 v1 -1.72%/trade (n 247, training) and
v2 FAIL (prereg_long_setups_2026-11-10, prereg_flag_v2_2026-11-10). Both watched only 30 minutes after the alert. The
owner's indicator differs: pole 3-8 bars of >= 3% and >= 1.5 ATR, flag 3-8 bars retracing 20-45% of the pole,
breakout close above the flag high on volume > 1.3x its 20-bar mean, 10-bar cooldown, and a strength score; and the
owner's XLAB signals came 1.5 and 2.5 hours after the alert, so this test watches the whole rest of the session.

## Indicator (exact port, default inputs; `sgt.fast.bullflag`, frozen at the study commit)
Per 1-minute bar i of the symbol-session (all stored bars of that day, extended hours included, state starting at the
day's first bar): ATR = Wilder ATR(14) on true range; VS = SMA(volume, 20); cooldown cd decremented each bar (floor 0),
scanned only when cd == 0 and ATR, VS are defined. For flag length fb = 3..8 (ascending), flag = bars i-fb..i-1:
flagHigh = max high, flagLow = min low; breakout = close_i > flagHigh and volume_i > 1.3 x VS_i. On breakout, for pole
length pb = 3..8 (ascending), pole = bars i-fb-pb..i-fb-1: poleHigh, poleLow, range = poleHigh - poleLow,
upMove% = (poleHigh - poleLow) / poleLow x 100, retrace = (poleHigh - flagLow) / (range + 0.0001), poleStrong = upMove%
>= 3.0 and range >= 1.5 x ATR at bar i-fb-1; flagShallow = 0.20 <= retrace <= 0.45; volOK = volume_i > 1.3 x VS_i and VS
at bar i-fb-1 > 0. The first (fb, pb) in that order that passes is the signal; then cd = 10. Strength = clamp(0, 100,
0.6 x upMove% + 25 x (1 - clamp01((retrace - 0.20) / (0.25 + 0.0001))) + 15 x min(3, volume_i / VS_i)).
Port check before the run (not a test of the edge): on XLAB 2026-11-12 SIP bars the port must fire near the owner's two
chart labels (strength 77.19 near the $1.80 breakout, 86.11 near $2.25). Small differences are expected (the chart's
feed volumes differ from SIP); a missing or far-off signal stops the study until the port is fixed.

## Population
Identical events to prereg_long_risk_2026-11-12 (the live long book's events: channel alert events with their t0,
cost_rt and split, plus the halt-reopen events), same training split (sessions before 2026-07-01) and held-out
(2026-07-01..2026-10-29). Signals counted: signal bar at or after the event's t0 and closing no later than 15:30 ET.

## Trade rules
Gate (all candidates): EMA9 > EMA21 on closes at the signal bar's close (EMAs over the day's bars, alpha 2/(n+1)).
Entry: buy at the OPEN of the bar after the signal bar (the signal is known only at its close). Skip if that open <=
flagLow. Stop: flagLow (stop checked first inside a bar: low <= stop -> sell at min(stop, open)). One position per
symbol at a time: a signal while a trade is open is ignored; every later signal after the exit trades. Net = gross -
the event's cost_rt (long book costs; no locate).
- **X1** (long book standard): target entry + 2R (R = entry - flagLow; high >= target -> sell at max(target, open)),
  else sell at the close of the bar 30 minutes after entry.
- **X2** (owner's trend: "9 stayed above 21"): no target; sell at the next bar's open after a bar closes with EMA9 <
  EMA21; at the latest at the 15:55 bar close.

## Candidates (alpha 0.05 / 4 = 0.0125, one-sided)
B1X1, B1X2: every gated signal. B2X1, B2X2: gated signals with strength >= 75 (the owner's threshold).
Statistic: mean net per trade (equal weight). Session-clustered bootstrap, 10,000 resamples, seed 20261113; p = share
of resampled means <= 0 (an empty resample counts against). Minimum training trades: 100 (B1), 50 (B2).

## Decision
ADOPT as a paper long-book setup (owner's OK) only if: training mean > 0 with p < 0.0125 and the minimum met, AND
held-out mean > 0 with n >= 30 (held-out opened only for candidates that pass training). Else FAIL -> PLAYBOOK section 4.
If more than one passes, the one with the higher training mean; ties -> X1 (fewer moving parts).

## Secondary (reported, never decides)
1. Same four candidates without the EMA gate (does 9 > 21 add anything?).
2. November 2026 forward events (2026-10-30..11-12): the four candidates, descriptive (n is small).
3. Owner's claim "an explosive green candle follows": share of signal bars whose next bar closes green and its mean
   return, vs all gated bars of the same events.
4. For each candidate, share of trades by exit (stop / target / time / trend) and the mean of the winners and losers.

## Not done
No other inputs (pole, flag, volume, cooldown, strength cut-offs) are explored; defaults and 75 are fixed here. The
owner's BoS / CHoCH indicator gets its own pre-registration once its Pine source is received.
