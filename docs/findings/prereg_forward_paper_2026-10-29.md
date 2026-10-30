# Pre-registration: forward paper test of two allocation candidates vs 60/40 (2026-10-29)

Committed BEFORE the first signal date (2026-10-30 close). Every number that decides this test
comes from prices that do not exist yet. Nothing below may change after the first signal.

## Sleeves (each a virtual $100,000, long only, no leverage, unallocated weight in SHY)
- **F1 dual momentum**: `sgt.allocation.signals.dual_momentum_top3`. Universe SPY IWM EFA EEM IEF
  TLT GLD DBC, cash SHY (VNQ excluded by the 2026-10-29 integrity rule). Top 3 by 252-day total
  return, 1/3 each, only those beating SHY's 252-day return.
- **F2 60/40 with trend crash-protection**: `sgt.allocation.signals.sixty_forty_trend`. Universe
  SPY, IEF, cash SHY. SPY gets 60% iff SPY's 252-day total return > SHY's, else that 60% is SHY;
  IEF gets 40% iff IEF's 252-day total return > SHY's, else that 40% is SHY.
- **B benchmark 60/40**: `fixed({"SPY": 0.6, "IEF": 0.4})`, cash SHY. Identical mechanics to the
  candidates, so the comparison is apples to apples. The real Alpaca paper 60/40 account is
  reported next to it for reconciliation only.

## Mechanics (the existing, unchanged `sgt.allocation.backtest.run`)
- Signal at each month-end close using data up to and including that close; execution at the
  next trading day's close (first trading day of the month, the same day the real 60/40 trades).
- Costs 5 bp per side on turnover. Weights drift with prices between rebalances.
- Prices: yfinance auto-adjusted daily closes (`data/etf_yf/adj_close_forward.parquet`, refreshed by
  `scripts/forward_paper.py`). Each refresh is cross-checked against Alpaca adjusted daily bars;
  a symbol with daily-return correlation < 0.99 over the forward window halts evaluation until
  resolved (reported, never silently substituted).
- First signal 2026-10-30; first execution 2026-11-02; the forward window starts at that execution.
- Each month `scripts/forward_paper.py snapshot` writes the sleeves' target weights to
  `reports/forward/YYYY-MM.json` (append-only; refuses to overwrite). Evaluation recomputes from
  prices; a mismatch with any snapshot is a defect to investigate before reporting.

## Decision (at the first evaluation on or after 2027-10-29, i.e. ~12 months)
Per candidate, over the forward window:
- **PASS** (eligible to replace or join the live 60/40, user decides): total return >= B's total
  return AND max drawdown no deeper than B's.
- **FAIL**: otherwise -> stays paper or is abandoned.
- Early abandon (checked monthly): candidate drawdown more than 10 percentage points deeper
  than B's at any month end.
Twelve months is short: a PASS is weak evidence on its own. It is read together with the
historical record in `docs/findings/prereg_etf_allocation_2026-10-29.md` (F1 = S3 there: post-2013
9.3%/yr vs 60/40 9.5%/yr; F2 was not tested historically before this registration).

## Reported monthly (not used for the decision)
NAV, total return, max drawdown, current holdings of each sleeve; B vs the real paper account.
