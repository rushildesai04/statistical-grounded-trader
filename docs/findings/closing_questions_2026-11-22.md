# Closing the open questions of the funnel audit (2026-11-22, phase 3b)

Follows `funnel_audit_2026-11-22.md`. Owner's brief: close seven questions without changing the frozen cohort. Real
money stays locked; no rule of either alert book changed; the forward sample (trades from 2026-11-23) was not read.

## 0. Answer and evidence table

**"The rule was followed" was true and is not the finding.** With the data corrected, most of the record remains:
8 of the 18 losing longs are real setups that lose on real quotes too, 10 of the 16 losing shorts carry no exclusion
flag and lose on real quotes too, and correcting the data also takes away 4 of the 6 long wins. Data defects are
real (8 long losses belong to orders or patterns that were not there; 5 short losses to orders that could not be
sold as assumed), but they do not explain the record. Separately, the base break the long book trades now is mostly not the
full tape's base break (the two agree on 4 of 22 alerts).

**Is the system suitable for further paper research?** Answered now where the evidence allows, dated where it does not
(section 7): the long alert book is NOT suitable for further strategy research on the free feed and continues only
as the frozen measurement; the short alert book is suitable for exactly one more piece of paper research (the sealed
measurement H1) besides its registered review. Owner review of both: Friday 2026-12-18. Final: 2027-02-26.

| | Item | Status | Evidence |
|---|---|---|---|
| **Verified engineering fixes** | Rate-limit test no longer depends on the clock | VERIFIED | `tests/ops/test_chartserver.py`, boundary test |
| | Nightly ledger reconciles against the database | VERIFIED | 53 of 53 on the runner; `exec_ledger.py --check` |
| | Decision record of every state change | VERIFIED in tests, live from the next deploy | `tests/ops/test_decisions.py` (12 tests) |
| | Decision record cannot hold a trading loop or fail silently | VERIFIED in tests | section 6; lock, missing table, disk failure, replay |
| | Nightly check that every channel alert reached the book | VERIFIED in tests | `sgt/ops/decision_health.py` |
| **Remaining deployment blockers** | Real-time full-tape data (setups, stops) | OPEN | section 3: 4 of 22; stops moved in 14 of 26 |
| | A stop resting at a broker | OPEN | the nightly ledger is research, not protection |
| | Locate availability AND price for shorts | OPEN | scan records availability only |
| | A legal, fillable order under the price test | OPEN | H1 specifies a model; only a real order validates it |
| | An alert posted during a listener restart is not replayed | OPEN, now detected the same night | section 2 (VRAX); mitigation in section 7 |
| | Any positive forward result | NONE | below |
| **Strategy results under corrected data** | Shorts with no exclusion flag (15) | -8.87% a trade (se 3.13), win 33% | `exec_baseline_2026-11-21.md` |
| | Shorts with a flag (14): not sellable as assumed | +5.93%, not a tradable figure | same |
| | Longs the model can price (18 of 24) | -3.63% (se 1.59); the other 6 were not valid orders | same |
| | Forward alerts with no flag (37) | -5.20% (se 2.45) | same; overlaps the shorts above, not a second confirmation |
| | Backtest split by the price-test flag | NOT SHOWN (difference -0.38 pt, interval -2.50 to +1.70) | `prereg_fade_price_test_2026-11-21.md` |
| **Unresolved hypotheses** | Why the unflagged shorts lose against a +1.76% backtest | UNEXPLAINED: no valid comparison exists | section 1 |
| | H1: a price-test short at a limit at the offer | REGISTERED, sealed, fill model specified, not run | prereg + Amendment 1 |
| | H3 spread and slippage against planned risk; H4 halt reopenings; H5 later alerts | NOT STARTED, and not to be started before 2026-12-18 | section 7 |
| | H2 full-tape setup validity | CLOSED for the past record (section 3); a forward label is proposed, not built | |

**Owner's decision, 2026-11-22 (after reading this document): option A, for a week.** "Longs remain frozen paper
measurement; shorts remain H1 plus the registered review. No new strategy search." The choice is separate from any
merge. It is revisited on Monday 2026-11-30, with the fifth health line (section 7).

**Three kinds of number are kept apart everywhere:** HISTORICAL results (what was booked, 2026-11-04 to 11-20);
CORRECTED SIMULATIONS of those same trades (the execution model, the full-tape setup check); and the FORWARD cohort
(trades from 2026-11-23), which nothing in this document or its follow-ups has read.

**Follow-ups of the same day:** the two operational risks named below as open are measured and tracked in
`ops_risks_2026-11-22.md`; the populations behind "24, 29 and 22" and the stop levels are set out at the top of the
result in `prereg_long_setup_tape_2026-11-22.md`; H1's locate charge is an assumption with its sensitivity fixed in
Amendment 2.

## 1. Compliance is not effectiveness: where each loss sits
Zero rule violations says the code did what the rule says. It says nothing about whether the rule is sound. Each
losing trade is placed in exactly one row below by a comparison that exists: the same order re-priced on real quotes
(`exec_model`, frozen run of 2026-11-21) and the same detector run on the full tape (section 3). Where no such
comparison exists the loss is called unexplained, not attributed.

### Longs: 24 settled, 18 lost

| Where the loss sits | Losses | Original average | Same orders on real quotes | Comparison used |
|---|---|---|---|---|
| Data defect: the order was not valid (ask already at or under the stop when booked) | 6 | -3.41% | no trade | the real quote at the booking |
| Data defect: the pattern is not on the full tape (SVRE, JMIA 11-20) | 2 | -4.42% | -4.84% | detector on the tape |
| Persists with corrected data, same setup on the tape | 5 | -4.46% | -6.31% | both |
| Persists, but the tape's version of the setup was at another minute (that trade was not priced) | 3 | -7.09% | -5.63% | both |
| Loss not reproduced on real quotes (ADXN, MRKR) | 2 | -2.55% | +9.08% | real quotes |

And the other direction, which a list of losses hides: of the 6 winning longs, 4 are losses on real quotes (GEVO,
RETO, GP 11-17, GMM; RETO's pattern was also thin-feed only). Net of both directions the 18 priceable longs average
-3.63% on real quotes against -2.25% as booked. **Correcting the data makes the long record worse, not better.**

### Shorts: 29 settled, 16 lost

| Where the loss sits | Losses | Original average | Same orders on real quotes |
|---|---|---|---|
| Order not sellable as assumed (no listed shares and / or under the price test) | 5 | -12.02% | -12.68% |
| Persists with corrected data (no exclusion flag) | 10 | -16.25% | -15.91% |
| Loss not reproduced on real quotes (TVRD) | 1 | -0.96% | +1.66% |

Of the 13 winning shorts, 9 carry a flag (+16.5%: not sellable as assumed) and 4 do not (+6.4%). Receipt delay is
not a cause: the same 29 trades priced from the bot's real decision time differ by -0.01 point (se 0.35), a paired
comparison.

### What stays unexplained
- Why the 15 unflagged shorts lose (10 of 15 on real quotes, six of them at about the 20% stop) when the backtest's unflagged fades
  average +1.76% (interval +0.62 to +2.93). Candidates, none tested and none assignable from 15 trades: chance;
  something particular to these three weeks; the 11 of 15 where the two borrow lists disagree; the live gates
  (news, beta, repeat) selecting differently from the backtest's. No valid comparison separates them.
- Whether the three longs whose tape setup came at another minute would have lost at that minute.
- Whether real candles would change the long result at all: the 2026-11-18 replay says the rule loses on them.

## 2. The funnel, reconciled completely
**Short side, all 801 channel alerts, mutually exclusive (one class per alert, in this order):** not a tested kind
261; a later alert of a stock that already had a live row that day 117; no row and unexplained 1; then by the row's
final state: outside hours 169, news 115, price 57, repeat 12, no print 12, switched off 12, no shares 8, beta 4, not
approved in time 4, traded 29. Sum 801.

**Long side.** The earlier report said "82 first alerts watched". That was wrong in one word: 82 is the number of
first qualifying alerts since the long book went live (2026-11-10); 66 of them were watched. Exact, mutually exclusive
(a long row is unique per alert), re-read from the database today:

| Class | Alerts | Note |
|---|---|---|
| Watched, no setup found on the feed | 37 | |
| Bought | 24 | |
| Seen while the book was off (recorded, texted as seen, never in a total) | 3 | 11-18, before 10:46 ET |
| Missed (the fill was seen too late to buy) | 2 | NOMA, TDTH 11-16 |
| Never watched: the long book was not yet running | 8 | 11-10, alerts before 11:45 ET |
| Never watched: the book was switched off, before watching-while-off existed | 8 | 11-13 (2), 11-16 before 12:13 ET (6) |
| **Total** | **82** | |

Besides these, 87 later alerts of the same stock-days never start a watch (one long event a stock a day, as tested).

**The unexplained first-day alert (VRAX, 2026-11-04 11:27:47 ET, "volume increasing").** The channel recorder stored
it 29 s later; the book has no row for VRAX at all. The book handled alerts before it (10:41 ET) and after it
(CHSN at 11:30:16 ET, row created within a second). A commit to the listener's code is timed 11:26 ET that day
(`63ced91`). Most likely the listener was restarting for a deploy when the alert was posted. PLAUSIBLE, not
confirmed: the platform's release log no longer reaches that day. What is confirmed by reading the code: the gap it
would need still exists. On start, the 15-second poll "only sets the baseline (nothing older than the start is
replayed)" (`chan_poll.py`), so an alert posted while the listener is down leaves no row. From this change on, the
nightly summary names any such alert the same night (section 6), and the process change in section 7 removes the
cause.

## 3. Full-tape setup verification
Plan frozen before the tape was loaded, control 29 of 29, run once: `prereg_long_setup_tape_2026-11-22.md` (plan and
result). Retrospective; its own script and output folder; nothing joins the nightly ledger or the forward cohort.

| | Existed | Existed, timing differed | Thin feed only | Insufficient evidence |
|---|---|---|---|---|
| All 29 recorded setups | 19 | 7 | 3 | 0 |
| ... first red candle (retired) | 17 | 4 | 1 | 0 |
| ... break of structure, halt reopening (retired) | 1 | 3 | 0 | 0 |
| ... base break (live now) | 1 | 0 | 2 | 0 |

**Existence is not equal risk.** The stop differs from the feed's by more than a quarter of the planned risk in 14
of the 26 that exist (12 further away, 2 closer; 5 at twice the book's risk or more; median planned risk 2.7% of
the price with the book's stop, 4.6% with the tape's). "Existed" says the pattern was there, not that the trade
taken was the trade the pattern defines. The 29 are the recorded setups (24 bought, 3 seen, 2 missed); the 22 in
the next sentence are a different population (today's rule re-run on all 82 alerts), of which only 3 were trades. For the rule live now, on all 82 alerts: both feeds show nothing on 60; of the other 22 they agree on 4,
show different minutes on 7, the feed alone shows a setup on 7 and the tape alone on 4.

So setup validity is NOT established for the long book as it runs today. It is a property of the data source, and
no rule change repairs it.

## 4 and 5. H1's fill model and its criteria
`prereg_short_price_test_limit_2026-11-22.md`, Amendment 1 (same day, before any code or outcome). The registered
population, read, seal and threshold are unchanged and say so. Added: a stricter fill model item by item (latency,
quote freshness, tick check under the price test, last in queue, eligible prints only, size and partial fills,
cancel in flight, fees, locate, borrow), which must ALSO meet the threshold; a mechanics-only validation that has to
pass before any return is looked at; and how the bound treats clustered days (session resampling, optimistic with 15
days), selection (three kinds) and the small sample. Stated in the plan: passing permits proposing one independent
follow-up; it does not establish an edge or authorize a trade. Stated as well: a model cannot validate fillability,
only a real order can.

## 6. The decision record's failure behaviour
A paper book has no order resting at a broker: its "protective exit" is the book's own decision, its row and its
text. The decision record sits after the row is written and before the text is sent, so the questions are whether
it can delay that text, lose a line, or do either unseen.

| Failure | Behaviour | Test |
|---|---|---|
| Database busy (locked by another writer) | the line waits at most 0.5 s on its own connection, then goes to the spool with its original time; the book goes on | `test_a_locked_database_costs_the_trading_loop_half_a_second_and_no_line` |
| Timeout | the same path; other writes keep their 30 s wait, the record does not use it | same |
| Table missing | spooled; the replay rebuilds the table and inserts | `test_a_missing_table_is_spooled_and_the_replay_rebuilds_it` |
| Disk failure (database and spool both unwritable) | the line is lost, logged as an ERROR and counted; nothing raises | `test_a_dead_disk_loses_the_line_loudly_and_still_does_not_raise` |
| Record unwritable for a whole trade | alert taken, entered, stopped out and texted as normal; three lines wait | `test_a_trade_is_booked_even_when_the_record_cannot_be_written_at_all` |
| Replay interrupted after its commit | no line is doubled | `test_a_replay_that_died_after_its_commit_does_not_double_a_line` |

**Health signal.** One line in the nightly summary, in every case (so a missing line is itself a signal):
`decision record: complete (N of N alert rows, L lines; C channel alerts of a tested kind)` or
`⚠️ decision record INCOMPLETE: ...`. It does not trust the writer: it compares the record with the books' own rows
and with the channel's own record.

**Recovery (runbook).**

| The line says | Meaning | Action |
|---|---|---|
| `N lines waiting in the spool` | the database was busy when they were written and the nightly replay could not insert them | nothing is lost; the next night retries. By hand, on the runner as the app user: `python -c "from sgt.ops.store import OpsStore; print(OpsStore().decide_replay())"`. File: `reports/ops/decisions.spool.jsonl` (`.replaying` while in work) |
| `N of M alert rows have no line` | a code path wrote a row without recording the decision | a bug in the hook, not in the trade: the row and the result stand. Find the path, fix, add its test |
| `N channel alerts never reached the book (UTC: ...)` | the listener was down or missed the message | no trade was taken; the alert is counted as an event without receipt in the forward sample; note it in the day's review |
| `N lines lost` | database and spool were both unwritable | check the volume; the trades' own rows are the record for those moments |
| `cannot be read` | the table or the database is unreadable | starting any job rebuilds a missing table; otherwise the database itself needs attention |

Known limits, named: the books' own writes of a trade's state still wait up to 30 s on a busy database (that is the
trade record itself, unchanged here; how often it waits has not been measured). And none of this is stop
protection: a stop resting at a broker remains a deployment blocker.

## 7. Decision dates: what each open item delivers, when it is complete, when it stops

| Investigation | Deliverable | Complete when | Stops when |
|---|---|---|---|
| Full-tape setup validity, past record | section 3 | done 2026-11-22 | closed; not re-run |
| Decision record and its health line | "complete" in the nightly summary | 5 sessions in a row say complete: Mon 11-23, Tue 11-24, Wed 11-25, Fri 11-27, Mon 11-30 (see below) | incomplete on 2 sessions: fixed before any other work |
| Trade-state database: waits for the write lock | the `database:` clause of the same nightly line | 5 sessions measured, counted from the session after the fix is deployed | a wait of 1 s or more: its holder is moved off the write lock before other work |
| Alerts posted while the listener is down | recovery design in `ops_risks_2026-11-22.md` (not built) | built on the owner's word, or after one more unrecorded alert | |
| Restart gap | deploys only outside 09:25 to 16:00 ET from now (a process rule, no code); the nightly line names any alert that still slips | 2026-12-18 with no unrecorded alert | one more unrecorded alert: a replay-on-start fix is built as its own change |
| H1, sealed measurement | variant code, mechanics-only validation, five hand checks | validation passes (target Fri 2026-12-04) | validation cannot pass: abandoned without a result. Read once at the 30th event or 2027-02-26 |
| Registered review, longs | verdict from the first 30 priced forward longs | 30th trade; one early look at 15 | as registered (retire / keep / inconclusive, one extension, 2027-02-26) |
| Registered review, shorts | verdict from the first 50 unflagged forward shorts | 50th trade; one early look at 25 | as registered |
| Daily books: three unverified items (live bars against test bars; ETF weights' timing; partial-month rebalance) | each confirmed or refuted with file and line | Fri 2026-12-04 | those three only |
| H3, H4, H5 | none | not started | not before 2026-12-18, and only by the owner's word |

**Which sessions count for the health milestone** (checked against the exchange calendar the books use,
`sgt.intraday.calendar`): a session counts when it is an exchange session, the build with the decision record ran
for the whole of it, and the 20:50 ET summary printed the line. Thursday 11-26 is Thanksgiving: closed, does not
count (the summary still runs that night and its line is not counted). Friday 11-27 closes at 13:00 and COUNTS: it
is a session. The build went live Sunday 11-22 at 17:19 ET, before Monday's open, so the fifth session is Monday
11-30 and its line is read that night. Any session whose line says INCOMPLETE restarts the count.

**Friday 2026-12-18, after the close (19 sessions of forward data): the owner's review.** One page, from inputs fixed
today, answering for each alert book "further paper research, measurement only, or stop":
1. Has a registered early look or review paused or retired it? (Its own rule; nothing here overrides it.)
2. Is the evidence whole: nightly ledger reconciled every night, decision record complete on at least 17 of 19
   sessions, the execution model able to price at least 90% of forward trades?
3. Is the data able to show what the book trades? Longs: a real-time full-tape source connected, or not. Shorts:
   H1's fill-model validation passed, or not.

A book that fails 2 is paused until the evidence is whole. A book that passes 2 and fails 3 continues as frozen
measurement only: no new research on it. Only a book passing all three is open to further paper research, and then
one registered experiment at a time. No answer on that date moves real money.

**Standing today, on today's evidence:** longs fail 3 (section 3) and are measurement only; shorts are limited to H1.
**Final date 2027-02-26:** the registered reviews and H1 all end by then; whatever is inconclusive then is reported
as not established, and the books stay paper-only or stop.
