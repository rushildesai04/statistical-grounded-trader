# Pre-registration: buy sudden drops we detect ourselves (the fade's mirror, long) — 2026-11-10

Committed BEFORE any drop event is detected or any return computed. Vault study `flush-long-2026-11`: training
2024-10-01 .. 2025-12-31, held-out 2026-01-02 .. 2026-10-16 (sealed; unlocked once, only after a training PASS).

## Why
Owner 2026-11-10: prioritise longs; "longs elsewhere". Every long on the channel's alerts lost (12 versions,
`prereg_long_family_2026-11-09`, `prereg_long_setups_2026-11-10`), and the fast long ideas tested before (the trader's
momentum rules, ORB stocks-in-play, intraday momentum, earnings continuation) failed too. The only working edge is the
fade: a burst of buying in a small cap reverts. Its mirror is a LONG: a burst of selling that reverts. Honest prior: our
own up-spike detector barely reverted (`prereg_self_fade_2026-11-06`, gross +0.11%), so the drop version may be ~0 too.

## Universe, event, trade
- Universe: `data/universe/gappers_2y.csv` symbol-days with prior-day volume >= 50,000; Alpaca SIP 1-minute RTH bars.
- Event (`sgt.fast.spikes.detect_drop`, at the CLOSE of bar t): close(t) <= 0.95 x close(t-5); volume(t) >= 5 x the mean
  of the prior 20 bars (>= 10 prior bars); a new low of day; price $1-50; entry bar t+1 inside 09:35-15:44 ET; first
  event per symbol-day.
- Trade: BUY at the OPEN of bar t+1; 20% stop below entry checked first in each bar (fill at min(stop, bar open)); sell
  at the close of the last RTH bar within 60 minutes. The fade's exit, mirrored. Cost: the measured gapper round trip
  at entry (`GAPPER_COST_MODEL`, open-hour multiplier); no locate.

## Gates (same as the self-fade study; `sgt.crypto.leadlag.gate`)
Training (run once): >= 200 events; mean net > 0 with a day-cluster bootstrap one-sided 95% lower bound > 0 (10,000,
seed 20261110); both calendar halves positive (split 2025-06-01); no month > 50% of the total. PASS -> held-out
unlocked once via the vault, same criteria with >= 100 events (split 2026-06-01). Held-out PASS -> a paper long book on
the runner (our scanner detecting drops live); real money only after 300 forward trades with the 95% CI above zero.

## Secondary (reported only)
Fall 3% / 10%; volume 3x / 10x; without the new-low condition; by entry window; 15-minute hold.
Trial ledger run ids `flush-long-2026-11-10-dev`, `flush-long-2026-11-10-holdout`.

## Result (2026-11-10, training run once, trial ledger `flush-long-2026-11-10-dev`) — FAIL; held-out stays sealed
| | value |
|---|---|
| events (primary: -5% in 5 min, 5x volume, new low) | 379 |
| mean net / gross | **+0.32%** / +1.07% |
| hit rate / stopped | 54% / 3.7% |
| day-cluster lower 95% | -0.39% (gate: > 0) |
| halves (split 2025-06-01) | +0.74% / -0.09% (gate: both > 0) |
| top month share | 57% (gate: <= 50%) |
Secondary (report only): fall 3% -0.02% (n 669), fall 10% **+1.06%** (n 95), volume 3x -0.43%, volume 10x +0.09%,
no new-low +0.05%; before 11:00 +0.70%, 11-14 +0.52%, after 14:00 -0.81%; 15-minute hold -0.57%.
Reading: sudden drops in small caps DO bounce (+1.07% before costs, the only clearly positive long so far), but
after the ~0.75% round trip the edge is too small and uneven to pass: concentrated in one month and gone in the second
half. Bigger flushes look stronger (fall >= 10%, n 95) but that cell was seen here, so it can only be tested on data
not yet looked at (its own pre-registration, held-out or forward). Nothing promoted; held-out sealed.
