# AAOI / AAOX (2x long) / AAOZ (2x short): "stable money" claim checked (2026-11-01)

Source: the user's trader ("1% in AAOI = 2% in AAOX; with the bearish AAOZ we can make stable money").
Data: Alpaca SIP daily closes, split-adjusted. AAOX trades since 2026-03-24, AAOZ since 2026-07-15.
Alpaca: AAOI shortable (easy to borrow); AAOX and AAOZ NOT shortable.

## 1. "1% in AAOI = 2% in AAOX": true for one day only
Daily beta AAOX +1.99, AAOZ -2.00 (tracking error sd 0.53%/day). Over weeks it breaks:
since 2026-03-24 AAOI -39.4% -> "2x" would be -78.8%, AAOX was -89.0%;
since 2026-07-15 AAOI -36.7% -> "-2x" would be +73.4%, AAOZ was **-35.3%** (the bear fund fell
while the stock fell). Cause: daily reset + AAOI's 7.6% daily volatility (volatility decay).

## 2. Buying both (hedged): loses by construction
2026-07-15 .. 10-30: 50/50 buy-and-hold -55.5%; rebalanced daily -3.5% (net zero exposure, fees).

## 3. Shorting both (harvesting the decay): looks stable, then blows up
Real funds, 76 days: +55.5% (no borrow fee), but not possible at Alpaca.
Fixed rule on 10 years of AAOI (synthetic 2x funds, no fund fees, equal short reset monthly):

| borrow fee | mean/month | months up | worst month | max drawdown |
|---|---|---|---|---|
| 0%/yr | -1.14% | 68% | -199.7% | -164% |
| 20%/yr | -2.81% | 57% | -201.4% | -116% |
| 50%/yr | -5.31% | 42% | -203.9% | -102% |

It wins two months in three, then loses about twice the position in months when AAOI rallies hard
(2023-06 +168%, 2024-11 +164%, 2026-04 +94%). Years: 2023 -242%, 2024 -77%, 2025 +136%.

## Verdict
No stable money. Long both = certain loss; short both = small frequent gains with account-ending
losses on a rally, and not executable at Alpaca. AAOX/AAOZ are only useful as a 2x one-day
directional bet, which needs a direction signal (none of ours has passed). Not pursued further.
