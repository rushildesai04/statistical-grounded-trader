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

## Result (2026-11-08, run once, trial ledger `fade-passive-exit-2026-11-08-dev`) — PASS as registered, but fill-model sensitive
| period | timed exits | fill rate (2 min) | delta per trade [lower bound] | filled / unfilled | net per trade |
|---|---|---|---|---|---|
| training | 1,616 of 2,345 | 78% | **+0.251%** [+0.223%] | +0.64% / -0.61% | +0.97% -> +1.22% |
| held-out | 528 of 764 | 77% | **+0.312%** [+0.273%] | +0.73% / -0.51% | +1.12% -> +1.44% |
Same at 1 and 5 minutes and in every price band.
Stress (after the run, conservative direction): requiring a print $0.01 below the bid: fill 55% / 49%, delta +0.06% /
+0.07%; $0.02 below: fill 40% / 36%, delta -0.03% / -0.01%. Most simulated fills come from bar lows exactly one tick
under an ESTIMATED bid (mid x (1 - median half-spread)), so the 1-minute-bar test cannot tell a real fill from an
odd-lot or off-exchange print. Not adopted on this evidence.

## Amendment A1 (2026-11-08, before any trade-level data is fetched) — queue-model test decides
For each timed exit: the ACTUAL NBBO at t (last quote at or before t; bid price and bid size) and every SIP trade in
[t, t + 2 min). Keep regular-way prints only (drop condition codes for odd lot "I", average price "B"/"W", out of
sequence "Z", derivatively priced "4", form-T/extended "T"/"U", prior reference "P", contingent "V"/"7").
FILLED when the cumulative size of kept prints at or below bid_t reaches bid_size_t x 100 (shares queued ahead of us,
round lots) + our order (5,000 / entry shares); fill = bid_t. Otherwise market buy at the ask of the last quote before
t + 2 min. Baseline = ask_t. Gate unchanged (training lower bound of mean delta > 0); held-out secondary; reported with
fill rate and filled / unfilled deltas. Run id `fade-passive-exit-queue-2026-11-08-dev`.
