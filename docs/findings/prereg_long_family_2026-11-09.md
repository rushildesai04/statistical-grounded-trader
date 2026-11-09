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

## Result (2026-11-09, run once, trial ledger `long-family-2026-11-09-dev`) — all three FAIL
| long | training n / mean / median / win / stopped [lower bound] | held-out mean | +3% / +8% target, 30 min (training) |
|---|---|---|---|
| L1 all | 2,345 / **-1.65%** / -5.45% / 41% / 51% [-1.86%] | -1.72% | -1.70% / -1.69% / -1.62% |
| L2 calm | 463 / **-0.87%** / -0.79% / 44% / 29% [-1.26%] | -1.11% | -0.85% / -0.81% / -0.80% |
| L3 reopen | 119 / **-2.12%** / -5.45% / 36% / 63% [-3.06%] | -3.52% | -2.71% / -1.50% / -2.27% |
Reading: buying the channel's alerts with day-trader stops loses at every target and hold tried. Half of L1 longs are
stopped within 15 minutes: the squeeze phase is too noisy for a 5% stop, and the drift after the alert is down.
After a halt BOTH sides lose (short -4.9%, long -2.1%): these are whipsaws, best not traded at all (which the
2026-11-09 entry fix now does). Calm alerts are a loss for shorts AND longs: no edge either way -> skip, not flip.
Nothing promoted; PLAYBOOK section 4 rows.
