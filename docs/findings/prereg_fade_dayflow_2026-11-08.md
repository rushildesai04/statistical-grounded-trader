# Pre-registration: same-day results and alert bursts vs the fade (2026-11-08)

Committed BEFORE any number below is computed. Owner (2026-11-08): keep testing; find techniques that make money.

## Why
Live days so far feel streaky (2026-11-06: every trade lost). Two untested, point-in-time levers:
1. **Breaker**: do the day's earlier results predict the next fade? If losing days cluster, stopping after early
   losses saves money; if not, a breaker throws away later trades, which are historically the best (finding 2).
2. **Burst**: many fade-eligible alerts in a short span may mean a hot small-cap tape where spikes keep running.

## Data
Champion per-trade official nets (`reports/fade_news/{dev,holdout}_events.csv`, column `net`) joined to entry times
(`reports/telegram/trades_pooled_first.csv`). A trade's result counts as KNOWN to a later trade only if its entry +
60 minutes is at or before the later trade's entry (the latest a 60-minute fade can close; earlier stop-outs are
ignored, which only delays information, never leaks it).

## Hypotheses and gates (training 2025-01-03 .. 2026-06-30; run once)
- **B (breaker)**: skip a fade when the last two KNOWN results of the same session are both losses (net < 0).
  PASS when n(skipped) >= 100 and the skipped trades' mean net has a session-bootstrap one-sided 95% upper bound < 0
  (10,000, seed 20261108), i.e. skipping them adds money.
- **U (burst)**: burst = fade-eligible alerts (this table) with entry in the 30 minutes before this one, same session.
  Skip when burst >= 3. Same PASS rule on the skipped group.
Held-out 2026-07..10 is reported as secondary only. A PASS becomes a forward shadow (n >= 300) and needs the owner's
OK before it changes the live rule; a FAIL is written into PLAYBOOK section 4.

## Secondary (reported only)
Mean net by number of known prior losses (0 / 1 / 2+) and by burst (0 / 1-2 / 3+); day-level correlation (mean net
of the first half of a session's trades vs the second half). Trial ledger run id `fade-dayflow-2026-11-08-dev`.
