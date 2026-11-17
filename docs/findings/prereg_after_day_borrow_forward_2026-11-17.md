# Pre-registration: the multi-day drift after an alert, on stocks IBKR actually lends, at the recorded fee (2026-11-17)

Status: FROZEN before any forward outcome of this rule is computed. Forward only: the rule is scored ONCE, when the
record below holds at least 300 borrowable alert days. Script `scripts/after_day_borrow_study.py` (`--count` shows
how many alert days are eligible so far and nothing else).

## Why
`prereg_alert_after_day_2026-11-17` measured the largest regularity in the data: from the close of an alert day the
stock is 2.5% lower a day later and 6.3% lower after five days (2,319 alert days, 2025-01 .. 2026-06; the median
stock -12.6% after five days). It could not be called a trade: a short held overnight needs shares every night, at
a fee, and no borrow history existed for those months. `prereg_day2_open_short_2026-11-17` then showed how fast a
cell picked from a map disappears on new data.
Since 2026-11-04 the runner has stored the IBKR stock-loan file every 15 minutes (`sgt.fast.borrow.snapshot`,
`data/borrow/YYYYMMDD/HHMM.parquet`: shares available and the annual fee for about 19,000 symbols). So from that
date the missing ingredient is being recorded, and the question can be asked honestly, forward: on the alert days
whose stock could really be borrowed at the close, did a short held one or five days make money after the spread
and the recorded fee?
No outcome of this question has been looked at: the after-day map used only 2025-01 .. 2026-06, rule D2 used the
next session's open-to-close of 2026-07 .. 10. Nothing after 2026-11-04 has been computed.
Prior: the drift is real; the open questions are how much of it lives in stocks that cannot be borrowed (the fade's
live record so far says most of that edge does) and what the fee takes. More likely a FAIL than a PASS.

## Record
Events: the forward research record's alert days (`reports/alert_fade/forward.csv`: every channel alert in the
tested cells, frozen rule, one per symbol-day), sessions from 2026-11-04 on. Closes: split- and dividend-adjusted
daily bars (as `alert_after_day_study`). Borrow: for a session, the LAST stored snapshot stamped at or before 16:00
ET of that session (none stored that day within 3 hours of the close: borrow unknown).

## Rule (fixed)
- **Borrowable at D**: the closing snapshot of the alert day lists the symbol with at least the shares a $2,500
  position needs at D's close (ceil(2500 / close)). Unknown or fewer: not an event of this rule (counted).
- **S1**: short at the close of D, cover at the close of D+1. **S5**: cover at the close of D+5, or earlier at the
  close of the first following session on which the closing snapshot no longer lists the needed shares (the lender
  can recall them: a forced cover) or is missing.
- Net = -(cover / entry - 1) - the event's modelled round-trip spread - carry. Carry = the highest annual fee seen in
  the closing snapshots of the days held, / 360, x the calendar days from D to the cover (weekends count). Returns
  clipped at -95% / +300%.

## Decision (once, at 300 or more borrowable alert days with their five following sessions)
PASS for a horizon: mean net above 0 and the one-sided session-bootstrap lower bound above 0 at alpha = 0.05 / 2
(10,000 resamples, seed 20261117). Reported with it: the share of alert days that were borrowable; the same trades
on the NOT borrowable days (what the paper would have claimed); the share of S5 trades ended by a recall; the fee
paid; the share losing more than 20% and the worst five (there is no stop in daily bars).

## What follows
- PASS: not a live rule. It goes to the owner with its numbers; playbook section 0 still requires a hard loss cap
  per trade and the owner's word for any short rule, and a version with an intraday stop would be tested first.
- FAIL: recorded; the multi-day short is closed for this channel.
- Before 300: nothing is computed but the count.

## Known limits
IBKR's list is one lender; daily closes; no stop; a recall is modelled only by the list at the close. The count
grows by about 8 alert days a session, perhaps half of them borrowable: 300 is roughly three to four months away.
