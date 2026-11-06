# Pre-registration: fade spikes we detect ourselves (no Telegram channel) (2026-11-06)

Committed BEFORE any event in this file is detected or any return computed. Vault study `self-fade-2026-11`:
training 2024-10-01 .. 2025-12-31, held-out 2026-01-02 .. 2026-10-16.

## Why
The live fade shorts the MetaTradings channel's alerts (+1.12%/trade held-out). Its mechanism is retail attention:
a burst of buying in a small cap that reverts. If that burst can be detected from price and volume alone, the edge
does not depend on one channel, the trade count can grow, and the test can use two full years of gapper data. If it
cannot, the channel's selection itself is the edge (worth knowing before relying on it with real money).

## Universe and data
`data/universe/gappers_2y.csv` symbol-days with prior-day volume >= 50,000 (the gapper scan; same filter as the
trader-rules studies), Alpaca SIP 1-minute RTH bars from the local store. Split by session date as registered above.

## Event (the spike; all at the CLOSE of 1-minute bar t, regular session)
- 5-minute run-up: close(t) >= 1.05 x close(t-5).
- Volume burst: volume(t) >= 5 x the mean volume of the prior 20 RTH bars (at least 10 prior bars required).
- New high of day: high(t) > every earlier RTH high that session.
- Price $1-50 at close(t); entry time 09:35-15:44 ET (as the live fade).
- First event per symbol-day.

## Trade (identical to the live fade from the entry on)
Short at the OPEN of bar t+1; 20% stop checked first in each bar (fill at max(stop, open)); cover at the close of the
last RTH bar within 60 minutes (`sgt.fast.exits.simulate(CHAMPION)`). Cost: the measured gapper round trip at entry
(`GAPPER_COST_MODEL`, open-hour multiplier) + 0.5% locate, exactly as the live fade's research.

## Gates
Training (run once): PASS needs >= 200 events; mean net > 0 with a day-cluster bootstrap one-sided 95% lower bound
> 0 (10,000 resamples, seed 20261106); both calendar halves positive (split 2025-06-01); no single month > 50% of the
total. PASS -> held-out unlocked once (`Vault("self-fade-2026-11").unlock`), same criteria (>= 100 events, split
2026-06-01). Held-out PASS -> paper forward on the runner as a second fade book; real money only after forward n >= 300.
FAIL at either stage -> written up; the channel remains the only fade source.

## Secondary (reported only)
Run-up 3% / 10%; volume burst 3x / 10x; without the new-high condition; by entry window (before 11 / 11-14 / after 14);
share of events that are within 5 minutes of a channel alert on the same symbol-day (overlap); mean net of overlapping
vs non-overlapping events.
Trial ledger run ids `self-fade-2026-11-06-dev`, `self-fade-2026-11-06-holdout`.

## Result (2026-11-06, training run once, trial ledger `self-fade-2026-11-06-dev`) — FAIL; held-out stays sealed
517 self-detected spikes, 2024-10-01 .. 2025-12-31 (peak memory 0.15 GB, streamed).

| | n | mean net | note |
|---|---|---|---|
| primary (5% in 5 min, 5x volume, new high) | 517 | -1.23% (gross +0.11%, lower 95% -2.10%) | halves -1.02% / -1.44%; 19% stopped |
| run-up 3% / 10% | 722 / 263 | -1.42% / -1.28% | |
| volume 3x / 10x | 830 / 199 | -1.10% / -0.31% | |
| no new-high condition | 895 | -1.21% | |
| by entry: before 11 / 11-14 / after 14 | 303 / 179 / 35 | -1.08% / -1.29% / -2.25% | |
| within 5 min of a channel alert (3.9%) | ~20 | +4.71% | small n |
| not alerted by the channel | ~497 | -1.47% | |

Reading: generic price/volume spikes in gappers barely drift down (gross +0.11%) and lose after the 0.9% spread and
0.5% locate. The few spikes the channel also alerted fell hard. The fade's edge is the channel's SELECTION (whatever
it sees beyond price and volume), not the spike pattern; the live book depends on that channel. Held-out sealed.
