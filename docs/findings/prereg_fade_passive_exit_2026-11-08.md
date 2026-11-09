# Pre-registration: cover the timed exit with a limit at the bid (2026-11-08)

Committed BEFORE any fill is simulated. Owner (2026-11-08): research techniques that make more money.

## Why
Real round trip ~0.85% (`docs/research/real_spreads_2026-11-08.md`) is close to the whole net edge (~+1.2%). The 60-minute
exit is not urgent (the hold curve is flat from 60 to 90 minutes, finding 3), so instead of buying at the ask we can
post a buy limit at the bid and wait briefly. Filled -> we save a full spread; not filled -> the price rose and we pay
the ask later (adverse selection). Stops stay market orders.

## Simulation (SIP data; timed exits only)
t = entry + 60 min (the champion's timed exit). Quotes at t (`reports/fade_spreads/quotes.csv`, exit leg): mid_t and
median half-spread hs_t; bid_t = mid_t x (1 - hs_t), ask_t = mid_t x (1 + hs_t).
- Baseline (market): cover at ask_t.
- Passive: buy limit at bid_t for 2 minutes. FILLED if an RTH 1-minute bar starting in [t, t + 2 min) has low
  STRICTLY below bid_t (a print through our price; conservative for queue position); fill = bid_t. Otherwise cover
  at the close of the last bar starting before t + 2 min, plus hs_t (market buy at the ask then).
- Trades stopped out, trades with no exit quote, and trades whose t + 2 min passes 16:00 ET: passive = baseline.
Delta per trade = (baseline price - passive price) / entry (positive = passive better).

## Gate (training 2025-01-03 .. 2026-06-30; run once)
PASS when the session-bootstrap (10,000, seed 20261108) one-sided 95% lower bound of mean delta per trade > 0 AND the
mean over UNFILLED orders is reported (adverse selection is part of the result, not hidden). Held-out secondary.
PASS -> the live book's timed exit switches to a resting bid in paper only with the owner's OK (an execution change;
the signal rule is unchanged); real-money use waits for the broker decision.

## Secondary
Fill rate; delta for filled / unfilled; by price band; 1-minute and 5-minute timeouts. Trial ledger run id
`fade-passive-exit-2026-11-08-dev`.
