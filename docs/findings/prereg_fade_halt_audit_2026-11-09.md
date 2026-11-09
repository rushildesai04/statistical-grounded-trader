# Pre-registration: halts right after the alert — a gap between the backtest and the live book (2026-11-09)

Committed BEFORE any number below is computed. Trigger: OMH 2026-11-09 (owner loss review, about -$1,160 live).

## The gap
The tested rule enters at the OPEN of the first SIP 1-minute bar starting at or after alert + 10 s, and only if that
bar starts within 5 minutes of the alert (`telegram_alert_test.attach_trades`, `MAX_ENTRY_DELAY`). When the stock is
halted (LULD) right after the alert there is no such bar: the event is `no_entry` and silently leaves the backtest; the
symbol's NEXT alert that day can then become its "first" event. The live book instead shorts at the latest IEX print
at alert + 10 s, which can be just before the halt (OMH: $27.04 at 09:38:33; no prints 09:39-09:47; reopened $29.20;
second halt 09:55-09:58; reopened $32.80 above the $32.45 stop). So the 3,113 tested fades exclude exactly the
events where the stock halted after the alert, and the live book trades them.

## Measurement (all channel alerts in the tested cells, both periods; run once)
Candidates: cell alerts posted 09:30-15:43 ET whose research status is `no_entry`. Live-style entry: the CLOSE of the
SIP bar that contains alert + 10 s (the last trades before the gap); none -> not tradable live either (excluded).
Price filter $1-50 on that entry. Exit = the champion's: from the next bar on, 60-minute window from the entry minute,
20% stop covering at max(stop level, bar open) (a halt reopen above the stop fills at the reopen), else the last
close; net = gross - modelled round trip (`cost_rt` at that price/time) - 0.5% locate.
Live semantics for "first event": per symbol-session the earliest candidate among {ok events, these halt events}.

## Reported
1. n and share of halt events vs ok events; their mean / median net, stop rate (training, held-out).
2. The corrected champion per trade = live-semantics population (first event per symbol-session, halt events
   included, a later re-alert dropped when an earlier halt event exists), vs the published +0.97% / +1.12%.
3. Decision rule fixed now: if halt events are >= 1% of events AND their mean net is below the champion's training
   mean, the backtest overstates the live edge -> (a) the PLAYBOOK records the corrected number; (b) a live guard that
   skips an alert when no SIP/IEX trade prints in the 30 s after alert + 10 s is pre-registered separately and scored
   in shadow (it is the live equivalent of the backtest's "no entry bar"). Otherwise recorded as no material bias.
Trial ledger run id `fade-halt-audit-2026-11-09`.
