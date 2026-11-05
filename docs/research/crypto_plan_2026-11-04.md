# Crypto program (owner decision 2026-11-04)

## Why crypto
The owner wants an always-on server texting trades within hours. Crypto trades 24/7; perpetual futures short
without a stock-borrow locate (the main blocker found for the stock fade: e.g. CRIS on 11-04 had 0 IBKR shares);
minute history is free. "Apply all TA and all news to every ticker" is NOT the method: testing hundreds of
indicators guarantees false winners (the large-cap and fast-ML studies failed exactly that way). Instead: few
pre-registered hypotheses, each with a development gate, a one-shot held-out test, and a forward paper record.

## Venue constraint
Hyperliquid's Terms of Use treat US residents and citizens as restricted persons; its PUBLIC data (funding,
candles) may be read for research, but it is not a trading venue for this account. Candidate US venue: Coinbase
(spot; US perpetual-style futures, 24/7). [VERIFY] which coins Coinbase lists as US perps, their fees, and retail
eligibility before any money.

## Tests (in order; each its own prereg)
| id | idea | data | status |
|---|---|---|---|
| C1 | BTC big minute -> alts follow (lead-lag) | Coinbase 1-min candles | prereg 2026-11-04 |
| C2 | Alt pump fade: short a coin after an outsized 15-30 min run-up (the stock fade's mechanism) | same | after C1 data |
| C3 | Funding-rate extremes: crowded perps revert | Hyperliquid public funding (research only) + candles | after C2 |
| C4 | News: headline classification at scale | needs a priced-in test design | only after a price-based pass |

## Data layer
`src/sgt/crypto/coinbase.py`: public candles, bounded responses, timeouts, retries; parquet per product-month
under `data/crypto/coinbase/`. Training months are fetched first; held-out months only after a dev PASS.
