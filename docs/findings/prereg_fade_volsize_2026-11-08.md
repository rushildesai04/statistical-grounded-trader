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

## Result (2026-11-08, run once, trial ledger `fade-volsize-2026-11-08-dev`) — FAIL as registered; the reverse is strong
Coverage 99%. Return per dollar: training equal +0.97% vs inverse-vol +0.17% (diff -0.81%, lower bound -1.04%);
held-out +1.12% vs +0.37%. Session Sharpe 2.51 -> 0.50. Inverse-volatility sizing is rejected.

The reason is the opposite effect: the fade earns MORE the jumpier the stock was before the alert. Mean net by sigma
quintile (training cut points 1.51% / 2.16% / 2.92% / 4.10% per minute, applied to both periods):

| quintile (calm -> jumpy) | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| training (n ~462 each) | **-1.32%** | -0.63% | +1.17% | +2.03% | +3.64% |
| held-out | **-1.18%** | +0.17% | +2.04% | +0.15% | +4.86% |
| stop-out rate (training) | 8% | 17% | 25% | 32% | 40% |

Robustness (found after looking, so descriptive): the calmest quintile is negative in every time-of-day bucket in
both periods, in every price band in training (3 of 4 held-out), for news and no-news alerts; the top two quintiles
beat the bottom two in 15 of 18 training months; sigma is unrelated to price (Spearman -0.09). Reading: a violent
spike overshoots and reverts; a calm stock that gets alerted is usually on a steady move that keeps going. Calm fades
rarely stop out; they lose by grinding. -> forward test `prereg_fade_calmskip_2026-11-08`.
