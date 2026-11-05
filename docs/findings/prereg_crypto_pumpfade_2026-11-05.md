# Pre-registration: crypto C2 — short an altcoin after an outsized run-up (2026-11-05)

Committed BEFORE any C2 event or return is computed. Program: `docs/research/crypto_plan_2026-11-04.md`.

## Hypothesis
The stock alert fade earns money by shorting small stocks right after an unusual burst up (held-out +1.12% per
trade). Retail-driven bursts in altcoins may overshoot and give back part of the move the same way. Unlike stocks,
a coin can be shorted with no borrow (perpetual futures), 24/7.

## Data (fixed; same files and split as C1)
Coinbase Exchange public 1-minute candles, the 19 alts fixed in prereg_crypto_leadlag (ETH, SOL, XRP, DOGE, ADA,
AVAX, LINK, DOT, LTC, BCH, SHIB, UNI, NEAR, AAVE, XLM, ATOM, FIL, ETC, HBAR, each -USD) and BTC-USD as the market.
Vault study `crypto-leadlag-2026-11`: training 2024-01-01 .. 2026-04-30; held-out 2026-05-01 .. 2026-10-31, still
not downloaded, fetched only after a C2 development PASS.

## Event and trade (primary, the only test that decides)
- Run-up r30 of a coin at minute t = close(t) / close of its last candle at or before t-30 min - 1 (same for BTC).
- Event: r30 >= max(3%, the 99.9th percentile of that coin's r30 over the 30 days before t, point in time, full
  window required) AND r30 - BTC r30 >= 2% (a coin-specific burst, not the whole market).
  One event per coin per 6 hours (later bursts within 6 hours of a kept event are skipped).
- Trade: short at the open of bar t+1. Stop: the first bar in t+1 .. t+60 whose high >= 1.10 x entry covers at
  max(1.10 x entry, that bar's open). Otherwise cover at the close of the last candle in t+1 .. t+60.
- Cost (primary): 0.10% round trip (perpetual-futures taker class). Funding over one hour is ignored (about
  0.001-0.01% at normal rates). [VERIFY] venue, listed coins and fees before any money (Coinbase US perps).

## Development gate (training only; run once) — identical to C1
PASS needs all of: >= 200 events; mean net > 0 with a day-cluster bootstrap one-sided 95% lower bound > 0 (10,000
resamples, seed 20261105); both calendar halves of training positive (split 2025-03-01); no single month > 50% of
the total. PASS -> held-out months downloaded and the same rule run once (same criteria, >= 50 events) -> 24/7 paper
texts on the runner, forward n >= 300 before any money. FAIL -> written up, nothing deployed, vault stays sealed.

## Secondary (reported only)
Hold 15 / 30 / 120 min; stop 5% / 20% / none; threshold 2% / 5%; no BTC-relative condition; gross; hit rate; per-coin
mean; US-session share; costs 0.02% / 0.30% / 1.20%.
Trial ledger run ids `crypto-pumpfade-2026-11-05-dev`, `crypto-pumpfade-2026-11-05-holdout`.

## Result (development gate, run once 2026-11-05) — FAIL
Training 2024-01-01 .. 2026-04-30, 19 alts, 1,416 trades (stop rate 2%, US-session share 40%).

| measure | value |
|---|---|
| primary mean net (60 min, 10% stop, 0.10% cost) | -0.42% per trade |
| gross | -0.32% |
| hit rate | 46% |
| bootstrap lower 95% | -0.68% |
| halves | -0.49% / -0.32% |
| hold 15 / 30 / 120 min, net | -0.21% / -0.21% / -0.59% |
| stop 5% / 20% / none, net | -0.40% / -0.40% / -0.40% |
| floor 2% / 5%, net | -0.39% (n 1,578) / -0.58% (n 729) |
| no BTC-excess condition, net | -0.41% (n 1,775) |
| per coin | 16 of 19 negative |

Reading: coin-specific bursts on liquid Coinbase alts keep running for the next hour rather than fading; every
variant loses before and after costs, larger bursts lose more. The stock fade's mechanism does not transfer to these
coins. Verdict FAIL; nothing deployed; vault stays sealed. The opposite direction (buy after the burst) is a new
hypothesis formed from this result, so it gets its own prereg whose decisive test is the sealed held-out period.
