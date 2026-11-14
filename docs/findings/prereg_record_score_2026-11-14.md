# Pre-registration: expected money from each stock's own record: skip calls with none (2026-11-14)

Status: FROZEN before any number below is computed. Script `scripts/record_score_study.py`, run ONCE on training; the
held-out slice is opened once, only on a training PASS. Sibling of `prereg_money_score_2026-11-14.md` (FAIL: the
forecast's size does not rank calls).

## Why
The owner's rule (2026-11-14): the score is the money a trade is expected to make or lose, and it decides whether the
trade is taken. After the first test the only expected-money number left for a model call is the average result of
earlier calls like it. On the full record that average is positive for long calls on 18 of 21 stocks and zero or
negative for short calls on 14 of 21, but those are whole-period numbers: picking stocks by them is hindsight. The
question that matters is causal: does a stock-and-side's record SO FAR say anything about its next calls?

## Data
As the sibling test: the walk-forward out-of-sample records of the 21 names; burn-in 2018-01-02 .. 2019-12-31,
training 2020-01-02 .. 2026-06-30, held-out 2026-07-01 .. 2026-10-28. Net per call = side x next close-to-close
return - cost (4 bp, RDDT 10 bp). Honest note: the whole-period per-stock averages have been seen (call_stats.json);
the walk-forward version below has never been computed.

## The score (known at the 15:50 ET decision)
For a call on stock i, side s, in calendar month M, using only calls with session before the first day of M:
- `n` and `mean` = count and mean net of earlier calls of the same stock and side;
- `pooled` = mean net of all earlier calls of that side, all stocks;
- expected net return `EV = (n x mean + K x pooled) / (n + K)` with K = 100 (an earlier record of 100 calls weighs as
  much as the side's pooled record; fixed here, not tuned). No earlier call of that side at all: EV = 0.
- GATE: the call is skipped when EV <= 0.

## Tests (one-sided, session-clustered bootstrap, 10,000 resamples, seed 20261114; Bonferroni 0.05 / 2 = 0.025 each;
## p = (1 + resamples with statistic <= 0) / 10,001: fail closed)
Statistic per side: mean over ALL calls of that side of (gated net - ungated net) = minus the summed net of skipped
calls divided by the number of calls. Positive = the skipped calls lost money, so skipping them paid.
- G-LONG: long calls. G-SHORT: short calls.
- PASS (per side): statistic > 0, p < 0.025, and at least 100 skipped calls in training (30 in held-out); fewer
  skipped = no verdict (FAIL). A side passes only if it passes on training AND on held-out.
- Reported, never deciding: calls kept and skipped per side with their mean net and win rate; per year; the names
  skipped most; the mean EV of kept calls against their realized mean (is the expected money honest in size?).

## Decision rule
- A side that PASSES: its gate is adopted for the score (owner's OK): a call of that side with EV <= 0 is shown with a
  negative expected dollar figure and not traded in the long-calls paper book (longs) / marked "no expected money" in
  the cards (shorts; the two long-and-short forward tests themselves are not changed).
- A side that FAILS: a stock's own record does not predict its next calls. Expected dollars for that side are then
  the side's pooled average x size for every stock (one number per side, no per-stock claim), and no call is skipped
  on a per-stock basis.
- Whatever the result: no real money, no change to the two long-and-short forward tests or to the deciding ledger of
  the long-calls test.
