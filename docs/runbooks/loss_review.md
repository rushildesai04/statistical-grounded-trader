# Loss review runbook (owner process, 2026-11-06)

The owner sends a written analysis whenever a trade loses. The job is a quant's, not a chart-reader's: find what
actually went wrong, separate bad luck from bad rules from bad execution, and only change a rule that wins on data.

## 1. Facts first (never reason from a screenshot alone)
`python scripts/loss_review.py SYMBOL YYYY-MM-DD` (on the runner; or `--db` with a copy of the ops DB). Check:
- the true entry and exit (live estimate vs the official SIP result), halts (missing minutes), the stop level;
- the signals at entry and the trade's group history (news / no news, time of day).
RAYA 2026-11-06: the alert came during a LULD halt; the live book showed a stale $1.425 entry while the stock reopened
at $1.52, so the "near-breakeven dip" in the screenshot was really +8-10%.

## 2. Classify the loss
- Execution error (the system did something the tested rule does not): fix in code with a regression test.
  RAYA: stale-quote entry during a halt -> fixed (`fade_live.STALE_QUOTE`).
- Rule loss within the expected distribution: about 25% of fades lose more than 15% (stop hits); a loss alone is not
  evidence the rule is wrong. Check whether this trade's group has an edge at all.
- New pattern: a proposed lesson that could apply to many trades -> step 3.

## 3. Turn every proposed lesson into an exact rule and test it
Write the rule precisely (what is measured, at which bar, acted on at which price), commit a pre-registration, run it
once on the 2,349 training fades with the champion guard, report the 764 held-out fades as secondary, and adopt only
on a pass (live changes need the owner's OK). Lessons already tested (all lose to the 60-minute hold with a 20% stop):

| lesson | file | per trade vs champion (train / held-out) |
|---|---|---|
| trailing stop / VWAP reclaim / EMA 9/21 cross / 15% target | prereg_fade_exits_2026-11-05 | all below |
| hold 2 h / to the close | prereg_fade_hold_2026-11-05 | below |
| learned exit (model at 10-50 min) | prereg_fade_exit_learn_2026-11-06 | +0.81% vs +0.92% |
| wait for breakdown + failed bounce | prereg_fade_confirm_2026-11-06 | -0.08% / +0.24% vs +0.97% / +1.12% |
| cover when a pullback fails | same | +0.87% / +0.07% |
| get out near breakeven after being underwater | same, addendum B | +0.38% / +0.01% |
| skip news trades | prereg_fade_news / RAYA review | same profit, half the trades (owner: keep trading) |
| take half at +3% / +5%, protect the rest | prereg_fade_confirm addendum C | -0.50% / -0.67% (+3%) |
| no stop / 10% / 30% stop | prereg_alert_fade_2026-11-01 | no stop +2.31% but -78% tails; 20% chosen for risk |

## 4. Report plainly
Lead with what went wrong, what was fixed, what was tested and the numbers; say which lessons look right on one chart
and lose over thousands of trades, and why.
