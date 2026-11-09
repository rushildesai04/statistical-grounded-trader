# Pre-registration: LONG trades on the channel's alerts (family of 3) — 2026-11-09

Committed BEFORE any long return is computed. Owner (2026-11-09): "why does it only go for shorts? ... we're not trying
anything new". Design: adversarial architect review 2026-11-09. The edge is the channel's selection (finding 11), so
every long uses the same alert population and the same entry clock as the fade (`fade.entry_start`).

## Common rules
Entry: BUY at the open of the first SIP 1-minute bar starting at or after alert + 10 s, within 5 minutes (the tested
fade entry), 09:30-15:44 ET, $1-50, first event per symbol-session. Stop 5% below entry: first bar with low <= level
fills at min(level, bar open). Target +5%: first bar with high >= level fills at max(level, bar open). Both in one bar
-> the stop (conservative). Otherwise exit at the close of the 15th minute. Net = gross - modelled round trip (no
locate: longs need no borrow). Stop fills are 1-minute-bar fills (same optimism as the fade; measured separately in
`prereg_fade_stop_slippage_2026-11-09`).

## The three longs
- **L1 all**: every fade event.
- **L2 calm**: only events with pre-alert sigma < 0.015072 (the calm quintile that LOSES as a short, finding 15).
- **L3 reopen**: the halt events of `prereg_fade_halt_audit_2026-11-09` (no bar within 5 min after the alert). Entry =
  the open of the first bar after the gap (the reopen), if it comes within 30 minutes; same stop/target/15 min.

## Gate (training 2025-01-03 .. 2026-06-30; run once)
Per long: mean net per trade, session-bootstrap (10,000, seed 20261109) one-sided lower bound at alpha 0.05/3 > 0 and
n >= 100 (L3: n >= 60) -> PASS -> forward shadow (paper), decided at forward n >= 300 (L3: 100). The held-out
(2026-07..10) is reported but is NOT independent for L2/L3 (their short returns were seen), so it cannot confirm.
Secondary: +3% / +8% targets, 30-minute exit, by time of day. Trial ledger run id `long-family-2026-11-09-dev`.

## Honest prior
L1 likely fails: the average path after an alert falls (finding 3); a tight stop truncates the squeeze. L2 is the
mirror of a known short loss and may net ~0 after costs. L3 is the most plausible (halted-up names kept running) but
small and fat-tailed.
