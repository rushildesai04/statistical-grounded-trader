# Pre-registration: systematic ETF allocation (2026-10-29)

Committed BEFORE any result is computed. Parameters come from the published literature; none
are tuned on our data. Nothing below may change after the run.

## Data
- Daily adjusted closes (dividends + splits) from yfinance, each ETF since inception; integrity
  check against Alpaca SIP daily bars (adjustment=all) on the 2016-2026 overlap: daily-return
  correlation >= 0.99 per ETF, else the ETF is reported and excluded.
- Universe (9 risk assets, multi-asset): SPY, IWM, EFA, EEM, VNQ, IEF, TLT, GLD, DBC.
  Cash: SHY (cash earns SHY's return).
- Common history starts when all ten exist (DBC: 2006-02); trading starts once every lookback
  is available (12 months later).

## Mechanics (all strategies)
- Signals computed on the LAST trading day of each month from closes up to and including that
  day; trades execute at the NEXT trading day's close (1-day lag, no look-ahead).
- Long-only, no leverage (gross <= 100%), unallocated weight sits in SHY.
- Costs: 5 bp per unit of turnover (each side), applied on every rebalance.

## Strategies
- **S1 (PRIMARY) Time-series momentum, volatility-scaled** (Moskowitz, Ooi & Pedersen 2012):
  asset i is held iff its 252-day total return exceeds SHY's; weight
  w_i = (1/9) * min(1, 0.10 / sigma_i), sigma_i = 63-day annualised volatility of daily returns.
- S2 GTAA trend filter (Faber 2007): w_i = 1/9 if the month-end close > its 10-month SMA of
  month-end closes, else 0.
- S3 Dual momentum top-3 (Antonacci 2014 style): rank by 252-day return; hold the top 3 at 1/3
  each, only those whose return exceeds SHY's.
- Benchmarks: B1 SPY buy-and-hold; B2 60/40 SPY/IEF rebalanced monthly; B3 equal-weight 1/9 of
  the nine risk assets rebalanced monthly.

## Periods
- Full: first trading month .. 2026-10-16.
- Post-publication: 2013-01-01 .. 2026-10-16 (after Faber 2007 and MOP 2012).
- Held-out: 2024-10-01 .. 2026-10-16 (most recent two years).

## Decision criteria (PRIMARY = S1, net of costs)
- PASS iff, over the post-publication period, Sharpe(S1) > Sharpe(B3) AND max drawdown(S1) <
  max drawdown(B3); AND over the held-out period S1's total return > SHY's.
- Otherwise FAIL: S1 is not deployed. S2/S3 are reported as secondary and are NOT eligible for
  deployment from this test (picking the best of three after the fact would be selection).
- PASS -> paper trading (monthly rebalance via the Alpaca paper account) for >= 3 months with the
  live drift check before any real capital.

## Reported metrics
CAGR, annualised volatility, Sharpe (excess over SHY), max drawdown, worst calendar year,
average annual turnover, and per-period tables.
