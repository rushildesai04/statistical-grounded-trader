# Pre-registration: buy BIG sudden drops (>= 10% in 5 min), decided on the sealed 2026 months — 2026-11-10

Committed BEFORE any 2026 event is detected. Vault study `big-flush-2026-11`; held-out 2026-01-02 .. 2026-10-16 (the
same months the failed `flush-long-2026-11` primary sealed; that rule FAILED on training, so its held-out will never be
used and this rule takes the period). Owner 2026-11-10: "Sealed 2026, then paper".

## Where the idea came from (training, already seen; NOT evidence)
`prereg_flush_long_2026-11-10` secondary cell "fall 10%" on training 2024-10..2025-12: n 95, mean net +1.06%, gross
+1.81%, win 61%, median +1.80%, 8% stopped; halves +3.12% (n 44) / -0.72% (n 51); top month 45% (2024-12). The second
half is negative, so the honest prior is that this fails too.

## Rule (frozen; code unchanged since 7a35bbf)
`sgt.fast.spikes.detect_drop(rth, run=0.10)`: close(t) <= 0.90 x close(t-5), volume(t) >= 5 x the prior 20-bar mean
(>= 10 prior bars), new low of day, price $1-50, entry bar t+1 inside 09:35-15:44 ET, first event per symbol-day.
BUY the open of bar t+1; 20% stop below entry (fill at min(level, bar open)); sell at the close of the last RTH bar
within 60 minutes (`flush_long_study.long_exit`). Cost: measured gapper round trip at entry; no locate. Universe:
`data/universe/gappers_2y.csv`, prior-day volume >= 50,000; Alpaca SIP 1-minute RTH bars.

## Gate (held-out, run once via the vault; trial ledger `big-flush-2026-11-10-holdout`)
n >= 50; mean net > 0; day-cluster bootstrap one-sided 95% lower bound > 0 (10,000, seed 20261112); both halves
positive (split 2026-06-01); no month > 50% of the total. If n < 50 the test is inconclusive (reported, not a pass).
PASS -> a paper long book on the runner (our scanner detecting 10% drops live, paper only); proof at 300 forward trades
with the 95% CI above zero. FAIL -> written up; buying drops is closed.
Reported only: hit rate, median, stopped share, by entry window, 15-minute hold.
