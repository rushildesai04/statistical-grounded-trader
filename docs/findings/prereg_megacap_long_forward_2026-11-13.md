# Pre-registration: forward paper test of the daily model's LONG calls only (2026-11-13)

Committed BEFORE the first long-only forward decision (first session: 2026-11-16). Uses only prices that do not exist
yet. Sibling of `prereg_megacap_forward_2026-10-30.md` (six names) and `prereg_megacap_ext_forward_2026-11-09.md` (the
other 15); those two tests are untouched and keep trading long and short.

## Why (owner 2026-11-13: "trading alphas when it makes sense"; go given the same day)
Review 2026-11-13 of the model's 2018-2026 out-of-sample record, all 21 names (already-seen data, so not evidence for
this test, only its motivation): long calls +0.23% per call (n 4,724, 53.8% winners) against +0.12% for an average
day, positive on 18 of 21 names and in 8 of 9 years; short calls positive on only 9 of 21 names. Long calls only:
+6.4%/yr, Sharpe 0.92, max drawdown -6%, against +4.4%/yr, 0.59, -11% for long and short. The choice of "long only" was
made on that record: exactly what a forward test is for.

## What is tested
No new model, feature or parameter. Each session the two existing forward tests write their decisions (15:50 ET, the
same frozen code and monthly models). This test takes every name whose position is +1 in either decision file (21
names in all) and is flat on every other name, including the model's short calls.

## Ledger (decides)
From the two forward mark files (`reports/megacap_forward/marks.csv`, `reports/megacap_ext/marks.csv`), sessions from
2026-11-16 on that are marked in BOTH files: per name the day's net is that file's `net` when position = +1 (close t ->
close t+1 minus the set's round-trip cost), else 0; voided rows are dropped; portfolio day = mean over the names marked
that day; days compound. Code: `sgt.megacap.longonly.ledger`.

## Paper book (shown, does not decide)
Strategy `megacap_long`: the long calls sized by `megacap_targets` under the same `MEGACAP_SIZING` caps as the other
model books (risk 1% per trade, 25% per position, gross 1.0, beta 1.0), auto-executed at the close, proposed only when
BOTH sets have a live 15:50 decision for today (otherwise no order that day: fail closed). Stop rule and drift monitor
as the other model books (cumulative below -5%).

## Evaluation
- Stop at any time if the ledger's cumulative net falls below -5%.
- Decision point 2027-04-30 (with the six-name test; about 115 sessions and, at the historical rate of one long call
  per name every 10 days, about 240 calls): CONTINUE if cumulative net > 0, else STOP.
- Reported at every evaluation, never deciding: calls, winners, average net per call against the tested +0.23% and
  53.8%; the same sessions for the two long-and-short tests and for simply holding the 21 names.
- No real money on this test alone (PLAYBOOK section 7: proven on unseen data and tradeable at a broker).

## Not done
No overlay, no leverage on long-call days, no per-name selection (the 3 names with negative long calls stay in).
