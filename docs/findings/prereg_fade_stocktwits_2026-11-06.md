# Pre-registration: StockTwits buzz at the alert vs the fade's result — forward only (2026-11-06)

Committed BEFORE any StockTwits data is recorded.

## Why
The fade's mechanism is retail attention (Barber, Huang, Odean, Schwarz): the most-bought small caps fall afterwards.
StockTwits' public stream measures that attention directly at the alert (`sgt.fast.social`, stamped on every live
fade trade from 2026-11-06 as `st_n60`, `st_span`, `st_bull`, `st_bear`, `st_watch`). No history exists, so the test
is forward only.

## Hypothesis and test (decided once, at forward n >= 300 live trades with known StockTwits data, >= 100 per group)
H: fades with HIGH buzz earn more than fades with low buzz. High = `st_n60` at or above the median `st_n60` of all
recorded trades at the decision point. Statistic: mean official net (high) - mean official net (low), session
bootstrap (10,000 resamples, seed 20261106), one-sided 95% lower bound > 0 -> PASS.
PASS -> buzz becomes a shadow sizing challenger (more size on high buzz), pre-registered separately. FAIL -> shown
as context only. Unknown (API down) trades are reported separately, never counted as low buzz.
Secondary: bull/bear tag share; span of the latest 30 posts; watchlist size; interaction with company news.
