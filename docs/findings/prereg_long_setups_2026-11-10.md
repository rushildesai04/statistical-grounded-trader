# Pre-registration: the trader's setups on the channel's alerts (long first) — 2026-11-10

Committed BEFORE any return of these setups is computed. Owner (2026-11-10): prioritise longs; the trader's setups are
the first red candle ("as soon as it goes down, first red candle, buy"), the bull flag ("two to three, maybe three to
four red candles"; "#1 pattern"), and break of structure (BOS). Owner answers: bull flag BOTH ways (buy the breakout;
short the failed flag); red candle: "test all, best wins"; "CGA" unknown, skipped. Shorts keep running meanwhile.

Known before this test (`prereg_long_family_2026-11-09`): buying AT the alert loses (-1.65%/trade, n 2,345). Every
setup here waits for a pullback or pattern first, which was never tested.

## Population and data
The 3,109 tested fade events (first event per symbol-session in the 10 cells, 09:30-15:44 ET, $1-50; dev 2,345 =
2025-01-03..2026-06-30, held-out 764 = 2026-07-01..2026-10-29). Start bar t0 = the tested fade entry bar (first SIP
1-minute bar at or after alert + 10 s). L7 uses the 119+ halt events of `prereg_fade_halt_audit_2026-11-09` with t0 =
the reopen bar (first bar after the halt gap, within 30 minutes). Bars: SIP 1-minute RTH bars (`data/alpaca`).

## Setups (code: `src/sgt/fast/patterns.py`, frozen at this commit; tests `tests/fast/test_patterns.py`)
| id | side | setup (decided on closed bars only) | entry | stop |
|---|---|---|---|---|
| L4a | long | first red candle (close < open) at or after t0, within 30 min | next bar's open | red candle low |
| L4b | long | same candle | buy-stop at the red candle's high, live 5 min | red candle low (cancel if hit first) |
| L5 | long | bull flag: pole >= +5% from its low within 15 bars, ending at its high; flag of 2-4 bars after it with >= 2 red, no new high, low >= pole high - 50% of the pole, mean flag volume < mean pole volume; flag completes at or after t0 within 30 min | buy-stop at the flag high, live 5 min | flag low (cancel if hit first) |
| S2 | short | the same flag failing | sell-stop at the flag low, live 5 min | flag high (cancel if hit first) |
| L6 | long | BOS: a bar closes above the last confirmed swing high (2 bars each side) after a confirmed swing low formed after it; from t0 within 30 min (swings may start 15 min before t0) | next bar's open | that swing low |
| L7a / L7b | long | L4a / L4b started at the halt reopen bar | as L4a / L4b | as L4a / L4b |

Common: one trade per setup per event (the first). Target = entry + 2 x risk (short: minus). Stop checked first in a
bar; a stop-entry that triggers and touches the stop in the same bar is a full stop-out; no target on the fill bar of a
stop-entry; a bar opening through the cancel level cancels; market entries at or beyond the stop are skipped. Exit at
the stop, the target, or the close of the last bar within 30 minutes of entry (or the session's last bar). Fills only on
bars starting at or before the last entry minute (15:44 ET; 12:59 on half days), as for the fade. Net = gross - the event's modelled
round trip (`cost_rt`, measured spread at the alert); S2 also pays the 0.5% locate. Fills are 1-minute-bar fills (the
fade's stop-slippage study applies to all stops).

## Gate (training only; run once; trial ledger `long-setups-2026-11-10-dev`)
Seven tests (L4a, L4b, L5, L6, L7a, L7b, S2). Per test: mean net per trade; one-sided p = share of 10,000 session
bootstrap means (seed 20261110) <= 0; Holm step-down at family alpha 0.05; n >= 100 (L7: n >= 60). PASS -> held-out:
CONFIRMED if held-out mean > 0 with one-sided bootstrap p < 0.05. CONFIRMED setups go to a paper forward book
(longs before shorts); proof as always: 300 live trades with the 95% CI of mean net > 0.

## Amendment A1 (2026-11-10, before any run; red-team review of commit 93769f6)
- Holm runs over all 7 tests: a test below its n floor stays in the family with p = 1 (it cannot pass).
- Held-out CONFIRMED also needs held-out n >= 30 (L7: n >= 15).
- Added a no-peeking test for the fill/exit simulator. No detector, fill or parameter changed.

## Secondary (reported, never decides)
30 vs 15 / 60 minute holds; 1.5R / 3R targets; trigger rate per event; news vs no-news; time of day; risk size
distribution (stop distance); same-event comparison with the short fade; a "longs first" book (the first confirmed
long setup that triggers on an event, else the fade) vs the fade alone.

## Honest prior
L4a likely loses: the first red candle is often the start of the reversal the fade earns from. L4b and L5 need the
price to turn back up first, so they skip the events that keep falling; they are the best long candidates but the
fade's +1.8% average says the typical path after these alerts is down. S2 shorts confirmed weakness and may beat the
plain fade per trade on fewer trades. L7 is small and noisy. A 5% pole and 2-4 bar flag are the standard day-trading
definition, not tuned; the detector will also be checked by eye on charts (owner and trader).
