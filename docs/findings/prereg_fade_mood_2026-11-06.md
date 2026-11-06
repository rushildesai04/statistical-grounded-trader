# Pre-registration: market mood at the alert vs the fade (2026-11-06)

Committed BEFORE any IWM data is downloaded or any number computed.

## Why
Owner (2026-11-06): every trade lost today; expects better. One untested lever: the market's mood. On small-cap
"risk-on" days retail buying is broad and hype spikes may keep running; on risk-off days they may collapse faster.

## Measure (point in time)
Mood = IWM (Russell 2000 ETF) return from its 09:30 open to the close of the last 1-minute bar that ended before the
fade's entry (SIP bars). Groups: risk-on >= +1.0%, risk-off <= -1.0%, neutral between.

## Hypothesis and gate (training 2025-01-03 .. 2026-06-30; run once)
H: fades on risk-on alerts earn less than the rest. PASS when (a) n(risk-on) >= 100; (b) mean net(risk-on) minus mean
net(rest) has a session-bootstrap one-sided 95% upper bound < 0 (10,000, seed 20261106); (c) skipping risk-on alerts
raises net per session with a paired lower bound > 0. Held-out 2026-07..10 reported as secondary (already seen for
the champion). PASS -> forward shadow (skip risk-on) decided at n >= 300; live change needs the owner's OK.

## Secondary (reported only)
Mean net by group in both periods; SPY instead of IWM; the opposite question (do risk-off days help?).
Trial ledger run id `fade-mood-2026-11-06-dev`.

## Result (2026-11-06, run once, trial ledger `fade-mood-2026-11-06-dev`) — FAIL
IWM minute bars downloaded for 2025-01..2026-10 (459 sessions). Training: risk-on (IWM >= +1% at entry) n 130
+1.50%/trade, neutral n 2,114 +0.94%, risk-off n 101 +0.91%; difference +0.53% (opposite sign to H, upper bound
+2.85%); skipping risk-on would cost -0.55%/session. Held-out (secondary): risk-on n 8 +9.6%, risk-off n 22 +6.6%,
neutral +0.87% (tiny groups). SPY gives the same picture. Reading: the market's mood does not hurt the fade; hype
spikes revert whatever the index does. No filter; nothing deployed.

## Context: the losing day that prompted this (2026-11-06)
Historically, on days with 3 or more fades, every trade lost on 2% of days and the day ended negative on 45%. All three
of 2026-11-06's trades were news trades (confidence v2 = 38); the confidence-sizing shadow (0.5x on such trades) would
have halved the day's loss.
