# Pre-registration: the daily model's long calls as a market timer (2026-11-17)

Status: FROZEN before any number below is computed. Script `scripts/dip_timer_study.py`, run ONCE on training; the
held-out slice is opened once, only on a training PASS. Owner decision 2A of 2026-11-17 ("next research: the model as
a market dip-buy timer").

## Why
`prereg_fresh_names_2026-11-17` (FAIL) showed what the model's long calls are: pooled, a call beat an ordinary day by
about +0.13%, but on the same day it beat the other stocks by only +0.036% (not significant). The calls bunch on days
after falls, when everything tends to bounce; the model adds WHEN, not WHICH. If that is true, the cleanest use of it
is not 21 single-stock positions but one position in the market on the days the model calls many longs: one
instrument, about a basis point of cost, no stock-picking noise.

Honest note on what has been seen (this matters for how much a PASS is worth):
- The hypothesis comes FROM these records. `prereg_long_rules_2026-11-15` (rule V) saw that the long calls' crowded
  days are where their money was made; `prereg_long_market_filter_2026-11-16` saw that calls below the market's trend
  earned more; the fresh-names run saw the pooled-versus-same-day gap. None of those looked at the MARKET's next-day
  return on those days, which is what is tested here, but the names are a large part of the index, so the training
  window is not independent evidence. A training PASS is therefore necessary, not sufficient: the judges are the
  held-out months (never opened for any model call) and then a forward record.
- Priors: next-day index reversal after weak days exists in high-volatility regimes (2020, 2022) and is otherwise
  small and unstable (Nagel 2012 for the stock-level version). A FAIL is at least as likely as a PASS.

## Data
The walk-forward out-of-sample records of the 21 names (`reports/megacap_daily_v2`, `reports/megacap_oos`,
`reports/aaoi_daily`), long calls only, as in the sibling tests: these are the names the runner decides every day at
15:50, so the signal is one that exists live. Market: SPY (primary) and QQQ (secondary), split- and
dividend-adjusted daily closes (`megacap_study.adjusted_daily`). Training 2020-01-02 .. 2026-06-30, held-out
2026-07-01 .. 2026-10-28. Cost 1 bp per round trip of the market position.

## Signal (every input is known at the 15:50 decision of day D)
- `breadth_D` = long calls made on D / names with a row on D.
- `cut_D` = the 80th percentile of `breadth` over ALL sessions before D in the records (expanding; at least 250
  earlier sessions, else no signal). Nothing of D or later enters it.
- **Signal day**: `breadth_D >= cut_D` and at least one long call. The market is held from D's close to the next
  session's close; otherwise cash.
One rule. No other percentile, lookback, universe or instrument is tried on these data afterwards.

## Decision (training)
Statistic: mean next-day SPY return on signal days minus mean on all other days of the window. One-sided test by a
bootstrap that resamples whole calendar months (signal days cluster in falling markets; 10,000 resamples, seed
20261117), p = (1 + resamples <= 0) / (n + 1), fail closed. PASS needs: difference > 0, p < 0.05, at least 150 signal
days, AND the signal days' own mean return after cost above 0 with its month-bootstrap lower bound (one-sided 95%)
above 0. Too few signal days = NO VERDICT, counted as FAIL.
Held-out (only on a PASS, once): CONSISTENT when the difference is above 0 and the signal days' mean after cost is
above 0; otherwise NOT CONSISTENT. About 80 sessions: direction only.

## Reported, not decided
Signal days: count, share of sessions, mean and win rate; other days the same. The timer as a book (in the market on
signal days only): total, Sharpe, largest drawdown, exposure; simply holding SPY the same. By year. QQQ in place of
SPY. Does the model add anything over a naive dip rule: the same number of days chosen as the days SPY itself fell
most (largest one-day falls, causal expanding cut at the same percentile), their mean next-day return, and the
overlap of the two day sets. The mean next-day return of the 21 names themselves on signal days (for scale).

## What follows
- PASS and held-out CONSISTENT: a nightly forward line (breadth, signal or not, next-day market return) and a
  proposal to the owner for a paper timer book; decided on its forward record, not on this file.
- FAIL: recorded; the model's breadth is not proposed again as a market timer; no variant is tried.

## Known limits
The names are part of the index (SPY about a third, QQQ more), so some of any effect is the names' own bounce. The
signal is decided at 15:50 and the position taken at the close: the last ten minutes of D are not in the signal.

## Result (2026-11-17, single training run; `reports/dip_timer/result_train.json`)
Plan `fa8ebb8`, code `ed38969`. 1,631 sessions, 352 signal days (22%).

**VERDICT: FAIL** (the difference is positive but not significant at the frozen level).

| training 2020 to June 2026 | SPY (decides) | QQQ |
|---|---|---|
| next-day return after a signal day | +0.157% (53% up) | +0.198% |
| after any other day | +0.039% (55% up) | +0.059% |
| difference | **+0.118%, p 0.067** (needs < 0.05) | +0.139%, p 0.080 |
| signal days after cost, lower bound | +0.147%, +0.047% (above 0: holds) | +0.188%, +0.058% |
| the timer as a book: total / Sharpe / largest drawdown | +60% / 0.66 / -15% | +80% / 0.68 / -20% |
| simply holding: total / Sharpe / largest drawdown | +152% / 0.81 / -34% | +250% / 0.90 / -35% |

By year, SPY next-day return on signal days (days): 2020 +0.73% (49), 2021 +0.14% (68), 2022 -0.03% (106), 2023
-0.16% (20), 2024 +0.20% (19), 2025 +0.21% (57), Jan-Jun 2026 +0.03% (33). The whole effect is 2020.

Does the model add anything over "the market fell hard today"? No.
| day set (SPY) | days | next-day return |
|---|---|---|
| model signal days | 352 | +0.157% |
| naive dip days (the largest one-day falls, same causal cut) | 340 | **+0.196%** |
| both | 151 | - |
| signal but not a naive dip | 201 | +0.063% (an ordinary day) |
| naive dip but not signal | 189 | +0.127% |

Reading: what the model's long calls "know" about the market is that it fell today. A rule that needs no model does
at least as well, and neither is a reliable edge: about a tenth of a percent, carried by 2020, lost in 2022 and 2023.
As a book the timer is in the market a fifth of the time and makes two fifths of what holding makes with a lower
risk-adjusted return. Held-out stays closed. The model's breadth is not proposed again as a market timer, and "buy
the index after a big down day" is not proposed from this file either (it was not the registered rule and its
numbers here are descriptive).
