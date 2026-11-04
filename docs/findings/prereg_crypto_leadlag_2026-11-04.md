# Pre-registration: crypto C1 — do other coins follow big BTC minutes? (2026-11-04)

Committed BEFORE any crypto price is downloaded. Program: `docs/research/crypto_plan_2026-11-04.md`.

## Hypothesis
When BTC makes an unusually large 1-minute move, liquid altcoins on the same venue move the same way over the
next few minutes (BTC leads, alts lag; documented in crypto lead-lag research). If true, shorting/buying the alts in
BTC's direction right after the BTC minute earns more than costs.

## Data (fixed)
Coinbase Exchange public 1-minute candles (USD spot books, `api.exchange.coinbase.com`, no account). A minute
without a candle had no trades. Leader: BTC-USD. Alts (fixed now, by size and Coinbase listing, not by any return):
ETH, SOL, XRP, DOGE, ADA, AVAX, LINK, DOT, LTC, BCH, SHIB, UNI, NEAR, AAVE, XLM, ATOM, FIL, ETC, HBAR (each -USD).
Split (vault study `crypto-leadlag-2026-11`): training 2024-01-01 .. 2026-04-30 (UTC); held-out 2026-05-01 ..
2026-10-31. Held-out candles are NOT downloaded until the development gate passes.

## Event and trade (primary, the only test that decides)
- Event: BTC 1-minute bar t whose |log return| (close vs previous close) is >= the 99.5th percentile of |1-minute
  BTC log returns| over the 30 days ending before bar t (point in time). Events within 10 minutes after an earlier
  event are skipped (no overlapping baskets).
- Basket: every alt with a candle in bar t+1. Direction = sign of BTC's move. Entry = alt's open of bar t+1 (the
  first minute after the BTC bar is known). Exit = alt's close of bar t+5 (5-minute hold), or the last candle
  available within that window. Equal weight across the basket; event return = basket mean.
- Cost (primary): 0.10% round trip per position (perpetual-futures taker class, ~0.05% a side). [VERIFY] Coinbase US
  perpetual-style futures fees and listed coins before any money; reported at 0.02%, 0.30% and 1.20% (retail spot).

## Development gate (training only; run once)
PASS needs all of: >= 200 events; mean net event return > 0 with a day-cluster bootstrap one-sided 95% lower bound
> 0 (10,000 resamples, seed 20261104); both calendar halves of training positive; no single month > 50% of the total.
PASS -> one-shot held-out test of the same rule (vault unlock, trial ledger); held-out PASS (same criteria, >= 50
events) -> 24/7 paper texts on the runner, forward n >= 300 before any money. FAIL -> written up, nothing deployed.

## Secondary (reported only)
Hold 1, 3, 10 minutes; per-alt mean; 99th / 99.9th percentile thresholds; gross (no cost); hit rate; share of
events in the US session vs overnight.
Trial ledger run ids `crypto-leadlag-2026-11-04-dev`, `crypto-leadlag-2026-11-04-holdout`.
