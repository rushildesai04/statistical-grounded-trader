# Forward record: the daily model's long calls on DOWN days only (2026-11-19)

Status: FROZEN before any session of this ledger exists. Owner, 2026-11-19, on the exploration
`docs/findings/book_cases_2026-11-19.md`: "Second ledger" (recorded beside the registered `megacap_long` book;
nothing that trades changes).

## Why
On the model's 2018 to 2026 out-of-sample record, a long call made on a day the stock was down 1% or more by the
decision earned +0.70% the next day on the 8 names the cut was first seen on and +0.58% on the 13 names it was then
checked on; a long call on any other day earned +0.18% / +0.11%, an ordinary day of those stocks. More than half of
the long calls carried no edge. That was read off data already seen (and the 2026-07 to 2026-10 calls were too few
to read), so it is a lead. A forward ledger is the clean test.

## What is recorded (no new model, no order, no text)
From the same two forward decision sets as `megacap_long` (`reports/megacap_forward`, `reports/megacap_ext`; 21
names), sessions from 2026-11-20 on that are marked in BOTH mark files and whose decision files are both readable:
- DOWN-DAY CALL = position +1 AND the stock's move on the decision day, as the decision file stored it at 15:50 ET
  (`exp(t_gap + t_r_intraday) - 1`, the same quantity as in the exploration), below -1%.
- Per name the day's net is the mark file's `net` for a down-day call, else 0; portfolio day = mean over the names
  marked that day; days compound. Exactly the registered ledger's arithmetic (`sgt.megacap.longonly`), on a subset
  of its calls.
- A session where a long call's move cannot be read is left out whole (never a partial day) and counted.
Code: `sgt.megacap.downday.ledger`. Shown as one line in the nightly summary next to the registered ledger.

## Decision (once, on 2027-04-30, the registered book's own date)
On the sessions recorded by then: PASS = the down-day ledger's total is above 0 AND its average net per call is
above the average net per call of ALL long calls on the same sessions. PASS -> put to the owner as the rule of the
long-calls book for its next period. Anything else -> closed; the cut is not tried again in another form.

## Known limits
Down-day calls are under half of all long calls (about 40% on the record): expect few calls a week. The registered
`megacap_long` test is not changed by this file in any way.
