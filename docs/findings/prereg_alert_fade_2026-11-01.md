# Pre-registration: fading MetaTradings "NEWS, VOLUME, BREAKOUTS" alerts (2026-11-01)

Committed BEFORE the held-out period of the alert test (sessions >= 2026-07-01) is examined. That
period was frozen by prereg_telegram_alerts_2026-10-29 and never scored because training failed.

## Why this hypothesis
The long test (prereg_telegram_alerts_2026-10-29, FAIL) found every alert cell negative before costs:
the stock has usually already run 11-26% in the 15 minutes before the alert. The mirror-image trade
(short at the alert) was explored on the TRAINING alerts only (2024-01 .. 2026-06, n = 2,349 first
alerts per symbol-day), in a throw-away script, on 2026-11-01:

| training, short at entry, cover after 60 min | mean net (gapper costs, no borrow fee) | median | win | worst 1% |
|---|---|---|---|---|
| no stop | +2.31% | +3.78% | 63% | -78.5% |
| stop 10% above entry | +0.74% | -2.87% | 45% | -13.0% |
| **stop 20% above entry** | **+1.47%** | +2.17% | 57% | -23.2% |
| stop 30% above entry | +1.74% | +3.11% | 60% | -31.3% |

Stock-call exits (prereg_trade_ideas_2026-10-29 data) were explored the same way; all six exit
variants were negative on training (-0.76% .. -1.09%/trade), so they are NOT tested further.

Feasibility check (2026-11-01, Alpaca assets API, 150 random training symbols): 21 easy-to-borrow,
0 hard-to-borrow-shortable, 97 not shortable, 32 inactive/delisted. Most of these names cannot be
shorted at Alpaca; a locate broker would be needed. This is the main practical risk and is reported,
not hidden.

## Frozen rule (primary, the only test that decides the verdict)
- Events: the alert test's `pooled_first` events (first parsed alert per symbol-day), held-out split
  (sessions 2026-07-01 .. 2026-10-29), status ok. Entry time and price exactly as in that test
  (`reports/telegram/trades_pooled_first.csv`: `entry_ts`, `entry`).
- Trade: short 1 unit at `entry`. Walk the regular-hours 1-minute bars from `entry_ts` (inclusive) for
  60 minutes. Stop: the first bar whose high >= entry x 1.20 covers at max(entry x 1.20, that bar's
  open) (gaps through halts are paid in full). Otherwise cover at the close of the last bar in the
  window.
- Net = (entry - cover) / entry - cost_rt - 0.50% (locate/borrow charge, flat per trade).
  `cost_rt` is the gapper cost model value already in the file.

## Statistics and verdict (computed once)
- One-sided 95% lower bound of mean net, session-clustered bootstrap (10,000 draws, seed 20261101).
- PASS requires all of: n >= 200; lower bound > 0; share of total net from the 5 best trades < 50%;
  mean net > 0 in both halves of the held-out period (split by time).
- INVALID (no verdict) if more than 10% of held-out events have no bars in the 60-minute window.
- FAIL otherwise.

## Secondary (reported, never change the verdict)
No stop; 15-minute exit; locate charge 1% and 2%; per alert cell (training cells with n >= 100);
subset of symbols that Alpaca lists today as easy-to-borrow (look-ahead: today's status, not the
status at the alert).

## What the verdict means
PASS -> forward paper record only (no capital): the bot logs a hypothetical short for every live
alert, scored monthly; plus a locate-availability check with a locate broker before any money.
FAIL -> the MetaTradings alert channel has no use as a trade signal in either direction.
Trial ledger run id: `alert-fade-2026-11-01`.
