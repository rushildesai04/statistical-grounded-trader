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

## Amendment A1 (2026-11-13, before the 2026-11-16 decision): a name that cannot be decided is flat that day
On 2026-11-13 the 15-name set made no live 15:50 decision: RDDT had 186 one-minute bars on the free real-time feed,
under the research floor of 195 (half the session), and that one name stopped the whole set (the six-name set decided
normally: all flat). From 2026-11-16, for the live decision of every set: a name needs prints in at least 60 minutes
of the session and one within 30 minutes of the market's last bar; a name that still cannot be built is FLAT that day
with the reason recorded in the decision file, and the other names are decided as before. If no name can be built
(for example no SPY / QQQ context) no decision is written, as before. Nothing else changes: models, features,
thresholds, costs, fills, evaluation. The research floor (195) is unchanged for training and the out-of-sample record.

## Amendment A2 (2026-11-20, owner's decision): the paper book gives a long call the ledger's weight
Nothing in the test changes: the deciding ledger, its stop (-5%), its date (2027-04-30), the models, the costs. Only
the paper book that is SHOWN beside it: from the first 15:50 run after the change is deployed every long call gets 1/N of the book, N = the
names decided that day in both sets (21), which is the weight the ledger gives it (`sms_control.long_call_targets`).
Until then the book was sized by `megacap_targets` (1% at risk over a day's range, at most 25% a stock): a lone call
got 25%, and on the 2018-2026 record that rule swung 2.4 times the ledger for a worse return per unit of risk (+13.6%
a year, Sharpe 0.83, worst drawdown -16.7%, against the ledger's +7.6%, 1.08, -5.0%), and stood 15% under its high when
the ledger first reached its -5% stop (`red_day_fixes_2026-11-20.md` section 5; already-seen data). Put to the owner as
1/21, 2/21 or keep; the owner chose 1/21. The book's sessions before that stay as traded at the
old size; they are not restated. The book and the ledger still differ in small ways (the book pays costs when a weight
changes, the ledger every day a call is on), so they move alike, not identically.

## Note N1 (2026-11-24): what the stop does, described as implemented. No rule, threshold or record changes
Owner, 2026-11-24: "Preserve current breach behavior, but describe it accurately: 'breach determined at close;
liquidation at the next eligible session close.' Do not equate that with 'stop at any time.'"

The Evaluation section says "Stop at any time if the ledger's cumulative net falls below -5%". What exists, and
stays, is narrower:

| Step | When | What |
|---|---|---|
| The breach is DETERMINED | at a session's close | the ledger is a close-to-close record; it has no value inside a session |
| It is WRITTEN | that evening (the 20:30 ET marking run) | the day's marks; the 20:50 summary shows "BELOW THE -5% STOP RULE" |
| It is ACTED on | 15:50 ET of the next session | the order job reads the ledger and sets every target flat |
| LIQUIDATION | that next session's close ("the next eligible session close") | the paper book fills at closes only. If that session has no live decision in both sets, the book is closed at the same close by the no-decision rule |

**This is not "stop at any time".** Between the close at which the breach is determined and the close at which the
book is flat lies one full session, with the calls that were filled at the breach close (they were decided ten
minutes before it, when the breach was not yet known): up to 21 calls of 1/21 each, the whole book. On the tested
record (2,218 ledger days, `config/models/long_ledger_history.json`) one session moved the ledger by less than 1% on
99 days in 100 and by -2.4% at worst; the live ledger has already had a worse one (-3.2% on the session marked
2026-11-19). So the ledger can end well under -5% before the book is flat. That exposure is accepted as is by the
owner's word above; any change to how or when the book exits is a separate proposal and review.

Batch-decided days (owner, the same day: "Keep future batch-decided days counted as registered, with explicit
labels. Do not remove observations retrospectively"): a session whose decision in either set was written after the
close stays in the ledger exactly as the Ledger section defines, and the nightly line names it. The paper book
never trades such a day. None has occurred from 2026-11-16 to 2026-11-23: every decision of both sets was live.
