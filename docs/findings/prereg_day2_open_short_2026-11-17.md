# Pre-registration: short the open the day after an alert day that closed weak (2026-11-17)

Status: FROZEN before the held-out period is read for this question. Script `scripts/alert_after_day_study.py --d2`,
run ONCE.

## Why
`prereg_alert_after_day_2026-11-17` mapped what an alerted stock does after the close. Its decision was about longs
(none works). One cell on the other side stood out and needs no share held overnight: after an alert day that
closed in the BOTTOM third of its range, the next session from open to close averaged -2.49% (943 training alert
days; median -2.56%, 39% up). A short opened at that open and covered at that close is an intraday short, like the
fade: shares are needed for hours, not nights. After the fade's costs (about 0.9% spread and 0.5% locate) that is
about +1.1% a trade, the size of the fade's own tested average.
This was SEEN in the training map, so training cannot test it. The held-out period (2026-07-01 .. 2026-10-29, 764
alert days) was not read by that study, by its own rule. It is the test.
How this differs from rule F8 (no short on a stock alerted in the prior 7 days): F8 is about the stock's NEXT ALERT,
a new spike, which keeps running. This rule needs no new alert: it shorts the open of the day after, whatever the
channel does.
Priors: the effect is large in training and has a known mechanism (the attention drift continues, and a weak close
says the buyers have left). But one cell out of 25 was picked after looking; some of it is selection. The held-out
sample has perhaps 300 such days and a per-trade spread of about 12%, so only an effect near its training size can
pass. A FAIL is as likely as a PASS.

## Rule D2 (fixed)
Event: a tested fade's alert day D (first tradable alert of the symbol that day, as the research defines it) whose
close is in the bottom third of D's high-low range (`close_pos <= 1/3`), from one adjusted daily series.
Trade: short at the open of D+1, cover at the close of D+1. Net = -(close / open - 1) - the event's modelled
round-trip spread - 0.5% locate. No stop (daily bars cannot see one); returns clipped at -95% / +300% as in the map.

## Decision (held-out, run once)
PASS: at least 150 events, mean net above 0, and the one-sided session-bootstrap lower bound of the mean net above
0 at alpha = 0.05 (10,000 resamples, seed 20261117). Otherwise FAIL.

## Reported, not decided
The training figure of the same statistic; held-out by news / no news; the share of trades that lose more than 20%
(what a 20% stop would have had to handle) and the worst five; the same rule on ALL alert days (not only weak
closes) and on strong closes, to show whether the weak close matters.

## What follows
- PASS: not a live rule. Next steps, each needing the owner's word (playbook section 0: shorts second, borrow and a
  loss cap inside the test): (1) the same rule re-run on 1-minute bars with the fade's 20% stop and halts; (2) a
  forward record with the IBKR shares and fee at each open, because no borrow history exists.
- FAIL: recorded; the day-after short is not proposed again.

## Known limits
No borrow in the test (the standing gap of every short here). Open and close prints are assumed tradable. Missing
delisted names help a short's measured result less than they hurt it (a stock that vanished fell), so this bias is
against the rule, if anything.
