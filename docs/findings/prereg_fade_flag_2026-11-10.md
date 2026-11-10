# Pre-registration: the bull flag as a risk control for the short fade — 2026-11-10

Committed BEFORE either rule's effect on the fade is computed. Owner (2026-11-10): the bull flag is "absolutely key for
shorts"; shorts are risky (unlimited loss), so they need a reason to be trusted. Bull flag both ways was tested as a
TRADE in `prereg_long_setups_2026-11-10` (all fail). While diagnosing it (training data, not a test) the plain fade
lost -6.83% per trade on the 247 events where a bull flag later broke upward, vs +1.84% overall. That conditions on
the future, so it is a hypothesis for an EXIT rule, decided here on data not yet looked at for it.

## Population, fade, detector
The 3,109 tested fade events and the tested fade (short at the entry bar's open, 20% stop with `cover()` fills, exit
at the close of the 60th minute; net = gross - cost_rt - 0.5% locate). The baseline must reproduce the stored fade
nets (`reports/fade_news/*_events.csv`) exactly; if not, the run stops. Detector: `sgt.fast.patterns.find_flag`
unchanged (commit 93769f6).

## Rules
- **X1 cover on breakout**: after a bull flag completes at or after the fade's entry (find_flag from t0), a buy-stop to
  cover sits at the flag high from the flag's `ready` time until the fade's own exit. A bar whose high reaches it
  covers at max(flag high, bar open), unless the 20% stop level is lower (then the stop rule applies first). No flag,
  or no breakout: the fade is unchanged.
- **X2 skip after a pre-alert flag**: skip the fade when a bull flag completed in the 15 minutes before t0 (find_flag on
  bars before t0 only, started at t0 - 15 min).

## Gates (each at alpha 0.025 = 0.05 / 2; session bootstrap 10,000, seed 20261111; trial ledger `fade-flag-2026-11-10`)
- **X1** is decided on the HELD-OUT months only (2026-07-01..2026-10-29; the hint came from training). Statistic: mean
  per-event change in fade net (X1 minus plain) over all held-out events. PASS: mean change > 0 with one-sided
  p < 0.025, the rule fires on >= 30 held-out events, and the no-news subset's mean change is not negative (the live
  book trades no-news alerts only). Training reported, not deciding.
- **X2** is decided on training (no flag-before-alert statistic has been seen) and confirmed on held-out. PASS: the
  skipped events' fade mean net < 0 with one-sided p < 0.025 and n >= 50 on training; CONFIRMED: held-out skipped mean
  < 0 with n >= 15.
A rule that passes becomes a paper rule for the live fade (texted as "flag broke out: covering") and is re-checked at
300 live trades. A failure is a playbook row.

## Honest prior
Earlier early-cover exits (trail, VWAP, EMA cross, failed pullback, breakeven) all lost: they covered on bounces right
before the second leg down (PLAYBOOK finding 3). A flag breakout is a stricter bounce signal, but it can be the same
trap. X2 may be small and noisy.
