# Pre-registration: size each fade by the stock's pre-alert volatility (2026-11-08)

Committed BEFORE any volatility number is computed. Owner (2026-11-08): research techniques that make more money.

## Why
Every fade gets the same dollars today. Risk-based sizing (smaller in jumpier names, larger in calmer ones) is the
standard way to raise return per unit of risk without predicting direction. Untested here; finding 12 warns the
cheapest (likely jumpiest) names carry most of the profit, so it may well fail.

## Measure (point in time)
sigma = standard deviation of 1-minute log close-to-close returns over the last 30 SIP RTH/pre bars that ENDED at or
before the entry (`ts + 1 min <= entry_ts`); fewer than 10 such bars -> unknown -> weight 1.
Weight w = clip(m / sigma, 0.5, 2.0), m = the median sigma of TRAINING trades (fixed; also used for held-out).

## Hypothesis and gate (training 2025-01-03 .. 2026-06-30; run once)
H: return per dollar deployed is higher with volatility weights. Statistic: sum(w x net) / sum(w) minus mean(net),
session bootstrap (10,000, seed 20261108), one-sided 95% lower bound > 0 -> PASS -> forward sizing shadow (n >= 300),
live change only with the owner's OK. Held-out 2026-07..10 secondary.

## Secondary (reported only)
Mean weight; session P&L Sharpe (equal vs weighted, same average dollars); mean net by sigma quintile; correlation
of sigma with entry price. Trial ledger run id `fade-volsize-2026-11-08-dev`.
