# Pre-registration: live spike-scanner experiment book (forward only) — 2026-11-09

Committed BEFORE the scanner runs live. Owner (2026-11-09): "execute more trades at a lower risk ... from other
stocks you are monitoring". The same detector failed its training gate (`prereg_self_fade_2026-11-06`: -1.23% per
trade after costs, n 517), so this is an EXPERIMENT at small size whose job is to measure, not to earn; it never
changes the channel fade.

## Universe (refreshed every 5 minutes, 09:30-15:44 ET)
Alpaca's top-50 gainers screener, symbols of 1-5 letters (warrant/unit/right suffixes W, U, R on 5-letter symbols
dropped), last price $1-50; plus every symbol the channel alerted today. At most 80 symbols.

## Event (live, on IEX 1-minute bars; the tested rule at the close of bar t)
close(t) >= 1.05 x close(t-5); volume(t) >= 5 x mean volume of the prior 20 RTH bars (>= 10 bars); high(t) > every
earlier RTH high today; price $1-50; bar t closes 09:35-15:44 ET; first event per symbol-day. IEX volume is a sample
of the tape, so the live event set differs from SIP's; the official result below is scored on SIP regardless.

## Trade
Short 0.5% of the scanner book's NAV ($100,000 start) at the first IEX print in bar t+1 (within 5 minutes, else no
trade); 20% stop; 60-minute exit or the RTH close. OFFICIAL (nightly, SIP): entry = open of bar t+1, cover by
`alert_fade_test.cover` over 60 minutes, net = gross - modelled round trip - 0.5% locate. Caps: no trade-count limit (owner 2026-11-09; runaway guard 40 open),
One summary text per day (in the digest), no per-trade texts.

## Decision (forward n >= 300 settled trades)
Report mean net with a session-bootstrap 95% interval. Upper bound < 0 -> retire the scanner. Lower bound > 0 ->
propose (owner decision) a larger size. Otherwise keep measuring. Secondary: by time of day, by price band, and the
pre-alert volatility quintiles of `prereg_fade_calmskip_2026-11-08`.

## Amendment A1 (2026-11-10): no scanner shorts on beta >= 3
Owner decision 2026-11-10 (emphasis "A: longs first"): shorts only without news and never on a stock with a beta of 3
or more; until now only the alert fade enforced it (amendment F7 of `prereg_alert_fade_2026-11-01`), while the scanner
ranked beta >= 3 names first (ground rule) and shorted them. On 11-10 the scanner shorted AMIX (beta 4.8) for -20.1%.
Rule from now: a spike on a stock whose beta (`sgt.fast.beta`, raw OLS vs SPY, 252 sessions) is >= 3 at detection is
recorded with status `filtered` (reason "beta >= 3 ...") and never traded, texted or counted in the book's NAV.
Unknown beta never blocks. The ground-rule ranking is unchanged.
Measurement kept: filtered spikes are scored nightly on SIP exactly like trades (same entry, cover, cost, locate) and
keep status `filtered`; the digest shows how many were skipped and what they would have averaged. The decision rule
above is evaluated on settled trades (beta < 3 or unknown) from 2026-11-10; the pre-amendment trades stay in the record,
and the secondary report adds the filtered spikes so the original rule can still be judged as registered.

## Amendment A2 (2026-11-11): scanner paused, still measured
Owner decision 2026-11-10 ("A: pause scanner"), after 11-10 had 7 shorts and 1 long: longs first, so the scanner opens
no shorts. The scanner was a measurement of a rule that failed its training gate (-1.23%/trade); on 11-10 it added 4 of
the day's 7 shorts. From the first run after this amendment (`scanner.ACTIVE = False`): every detected spike is recorded
with status `filtered` (reason "scanner paused ...") and scored nightly on SIP exactly as in A1; nothing trades, texts or
moves the book's NAV. The digest RECORD line says "paused (longs first)" and, once spikes are scored, what they would
have averaged. The universe and its beta ranking keep running so the measurement continues. Resuming = setting ACTIVE
back to True in a new commit, with the owner's OK.
