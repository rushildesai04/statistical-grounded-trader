# Operational risks: adversarial review and register (2026-11-23)

Owner, 2026-11-23: "Review every outstanding operational risk from the recent audits. Do not merely carry the list
forward, and do not close an item just because no incident has occurred." Each risk below has its code path and
trigger, what it can affect, how it was challenged (reproduced, disproven, or guarded), and its status. Paper only.
No trading rule is changed here; the one rule question that came up is listed for the owner (section 6) and not
acted on. An independent red-team of the stop-check path and independent reviews of each code change were used;
their findings are marked.

Classes used: **DEMONSTRATED** (reproduced in a test or on the runner), **PLAUSIBLE** (a real path, not reproduced),
**CANNOT OCCUR** (shown, with the reason), **NOT APPLICABLE WHILE PAPER-ONLY** (a requirement for real orders, kept in
section 5).

## 1. Running version and what was verified

| | |
|---|---|
| Running | runner **v109 = main `87b21c8`**, deployed Sun 2026-11-22 23:59 ET (gate 2,267 passed) |
| Merged in it | #201 (first start after the last record, replay off the event loop, rows without identifiers recognised, `missed_why`, the drill) and #202 (recovered shorts record-only). Diff `5be1ae9..87b21c8` on the trading path: the record-only rule, the legacy-row guard, a local helper with the same reason strings, `missed_why`. Nothing else |
| Targeted checks on `87b21c8` | 163 tests of the alert, short, long, decision and lock paths pass; the recovery drill passes 12 of 12 in record-only mode |
| Recovered shorts | recorded with `via = replay`, posting and recovery time; never entered, also with a fresh print after the recovery (tests, drill check 7). Reason "recovered after a restart: recorded, not entered" |
| Repeat rule for a recovered alert | unchanged definition (`fade.prior_alert`): an operational skip is an event, as "alert received late" and "paused" are; a row a rule filtered keeps that rule's reason; only "no print" skips are not events. Tested: the recovered alert counts as a prior alert the next day, and the long book still watches it |
| First production restart with a cursor (v109, 23:59 ET) | the catch-up ran from cursor 27263, reached the newest message, wrote no gap and no duplicate; `alert_catchup` ok at 23:59:27 ET. **Nothing was posted during the restart, so no alert was recovered.** Verified: start-up with an existing cursor, the push gate opening, state intact. NOT yet observed in production: a real post recovered as `replay`; a backlog over 100 messages; Telegram's "slow down" answer during a catch-up |
| First start of #200 (v108), restated with the channel's own record | cursor set at 27263; ids 27257 to 27263 were posted Fri 16:25 ET to Sat 23:50 ET, all handled by the build then running; the newest stored message was 27263, so nothing was posted during that restart either |

## 2. Effective versions (for the cohort's accounting)

Every version below went live BEFORE the forward cohort's first session (Mon 2026-11-23 09:30 ET), provided v110 is
deployed before 09:25 ET; otherwise v110 takes effect from Tue 2026-11-24 and Monday's decision lines carry the
shorter label, read with this table.

| Runner | main | Live from (ET) | What changed that the accounting needs to know |
|---|---|---|---|
| v107 | `e6d0d30` | Sun 11-22 23:09 | reads without the write lock (journal page); lock waits recorded |
| v108 | `5be1ae9` | Sun 11-22 23:24 | recovery: durable cursor and identifiers; replay label; **restart corrections**: a short's entry window is not stretched by downtime, no entry on a print from before the listener was back, a long fill on a candle that began before then is `missed` |
| v109 | `87b21c8` | Sun 11-22 23:59 | **recovered shorts record-only**; first start after the last record; replay off the event loop; `missed_why` on a missed long's decision line |
| v110 | this change | after merge | **rule-version labels name the restart rules** (the labels of v108 and v109 did not: a record gap found in this review); stop checks first in every pass; alert handling off the event loop; late stop checks recorded |

Reasons kept on the record, by which every restart case can be counted: short rows "alert received late",
"recovered after a restart: recorded, not entered", "no trade print after the restart within 5 min of the alert";
long rows `missed` with `missed_why`; identifiers `via = replay` with both times; nightly ledger rows `via` and
`recovered_at`. The frozen sample (settled trades with no exclusion flag from 2026-11-23, in order) is unchanged.
All of these can only remove a restart-case trade from it. A with/without-recovery figure is a diagnostic beside
the registered calculation.

## 3. Fixed in this change

| # | Risk, path, trigger | Can affect | Class and evidence | Fix | Regression check |
|---|---|---|---|---|---|
| 1 | **A waiting alert delays the stop check of open positions.** `fade.monitor` walked every row in id order on one thread; a row waiting to enter makes up to four network calls a pass (news, beta, quote, halt check; 5 s timeouts) for up to five minutes when its stock is halted. Same shape in `long_book.tick`: up to 15 bar requests for candidates before the open longs' exit check | stops, exits, the time of the exit text (which the official scoring of a texted trade reads) | DEMONSTRATED (independent red-team: 4 waiting rows at 2 s each put the open row's stop check 8.1 s into a 10.2 s pass; the next pass starts 10 s after that) | open positions are checked FIRST in every pass and again whenever 10 s (shorts) / 15 s (longs) have gone by while entries are worked on. A long opened in a pass still gets its first exit check in that pass | `test_the_stop_of_an_open_short_is_checked_before_any_waiting_alert_is_worked_on`, `..._checked_again_while_a_long_run_of_waiting_alerts...`, `test_open_longs_are_checked_before_candidates_are_read...` |
| 2 | **Alert handling on the event loop.** A pushed alert (`on_new`), an edit (`on_edit`), the research record and the flag read of the channel loop all did database work on the loop thread; while such a write waits for the lock no loop's next pass can be scheduled | stops, exits (delay), alert receipt (delay) | DEMONSTRATED (test: a push that takes 2 s held the exit of an open short until it finished; also found independently by the red-team) | all of it off the loop: alerts on the alerts thread (one alert at a time, push and catch-up alike), the rest on pool threads | `test_a_pushed_alert_whose_database_write_is_slow_does_not_hold_an_exit` (fails with the push back on the loop) |
| 3 | **Every flag read took the write lock.** `OpsStore.flag` used the write transaction; `paused`, the off switches and arming are read on the stop-check path | stops, exits, entries (delay behind any writer) | DEMONSTRATED (test: with a writer holding the lock the read waited; now returns at once) | `flag` is a read; the short book's row selects are reads | `test_reading_a_flag_does_not_wait_for_a_writer` |
| 4 | **No headroom in the shared worker pool.** Six loops, six workers (2 CPUs + 4); one more user and a stop check could wait for a worker held by a slow research call | stops, exits | CANNOT OCCUR today (counted: each loop holds at most one worker), PLAUSIBLE after any addition (found independently by the red-team) | the two books' passes run on threads nobody else can take | `test_a_pass_on_its_own_thread_does_not_wait_for_the_shared_workers` |
| 5 | **A listener that hangs or keeps crashing in session was silent until 20:50 ET.** Only the 09:20 check and the evening summary read the heartbeat | every stop and exit, alert receipt, the owner's picture of the day | DEMONSTRATED (no reader of the heartbeat between 09:20 and 20:50) | the per-minute control job (a separate process) texts when the heartbeat is over 3 minutes old in alert hours, again every 30 minutes, at most 6 a day; every start after a silence is written down and the nightly line warns about one in alert hours | `tests/ops/test_watchdog.py` (5), `test_a_restart_inside_alert_hours_is_a_warning...` |
| 6 | **"Stop checks run every 10 seconds" was never measured** | the validity of every statement about protection | unavailable evidence | every stop check that starts more than 25 s (shorts) / 35 s (longs) after the one before it is written down; nightly clause `stop checks:` | `test_a_pass_that_starts_late_is_written_down...` |
| 7 | **A wrong page from the channel would skip alerts silently.** The catch-up asks for "the oldest 100 after the cursor"; a full page of the NEWEST 100 would move the cursor past everything older | alert receipt (lost alerts, unrecorded) | PLAUSIBLE. Evidence for the library: its source; one production run of the research recorder stored 195 messages through the same reverse paging (2026-11-04). Not observed: a backlog over 100 through the catch-up itself | a full page is checked against the oldest message after the cursor; a mismatch is an error with the cursor untouched and a nightly warning | `test_a_channel_that_answers_a_full_page_with_the_newest_messages_is_an_error_not_a_silent_skip` |
| 8 | **Rule-version labels did not name the restart rules** (section 2) | cohort accounting, owner decisions read from the decision record | DEMONSTRATED | labels extended; effective versions tabled | `tests/ops/test_decisions.py` reads the label by reference |
| 9 | **The channel loop could end on an exception outside its try** (the wait computation) | alert receipt by the 15-second check | PLAUSIBLE (red-team) | wrapped; the loop sleeps and goes on | source check in `test_chan_poll.py` |
| 10 | **The deploy script did not enforce "no deploy in session".** The rule was mine, in a document | alert receipt and stops during a restart | DEMONSTRATED (the script's window check had no session hours) | the script refuses 09:25 to 16:10 ET | window table checked by hand (16 times) |

Also from this review, a diagnosis aid: a slow database wait now names the code that asked for the write, not the
store helper (`... via set_flag`).

## 4. Disproven or closed, with the evidence

| Claim challenged | Result |
|---|---|
| Two processes (the listener, a cron job, the tape check) can close or enter the same row twice | CANNOT OCCUR: every state change is `UPDATE ... WHERE id=? AND status=?` inside the single write lock; the loser changes nothing. Traced by two independent reviewers; exercised in the rollback run (two builds on one database) |
| A second delivery of an alert (push and check, replay, retry) duplicates a row, decision line, entry or text | CANNOT OCCUR: the durable identifier is checked inside the write. Tests; drill check 11 (kill, cursor wound back, same message pushed again) |
| A stop-check loop can die quietly while the process lives | CANNOT OCCUR: each loop body is wrapped whole; a lost Telegram connection ends the process, which the supervisor restarts (red-team, verified). A hung process is now caught by row 5 |
| A stop check can wait for a worker thread | closed by row 4 |
| The decision record can block or be lost silently | closed in #197: 0.5 s bound, spool, nightly replay and health line; tests for a locked database, a missing table, a dead disk |
| The disk can fill | the volume is at 40% (2.1 of 5.2 GB); the files this work added are bounded (2 MB, 1 MB, 5 MB); the 09:20 check texts under 10% free. Closed, monitored |
| A rollback corrupts state or duplicates actions | closed in #201 (run on the real previous build; a duplicate filtered row fixed); rollback only through the deploy script, which refuses while a row waits or is open |

## 5. Not applicable while paper-only: requirements for real orders (kept, not closed)

| Requirement | Why it is not a defect of the paper book |
|---|---|
| A stop resting at the broker | paper positions have no broker; every stop here is a check by this process. For real money this process must never be the only protection |
| The stop when a stock is halted or silent on the free feed: `fade._exit` has no price, so it marks the trade at its last print and waits (red-team) | the OFFICIAL result is computed from the full tape by the research rule whatever the live estimate did; the live text can be late by up to 16 minutes (the tape check). Real money needs real-time full-tape data and a broker stop |
| A crash between a state change and its text loses the text | the paper record is right, the text is missing (never doubled). Real orders need idempotency keys at the broker and a reconciliation on start |
| Borrow, locate cost, the short-sale price test, queue position, partial fills | the nightly modeled ledger prices them (a research ledger, not stop protection); H1 is registered for the price-test case |
| Durability against power loss versus commit speed (`synchronous`) | left at the durable setting; changing it trades lost commits for speed and is not needed on the evidence so far |

## 6. Open

| Risk | Concrete impact | Safeguard now | Evidence or capability missing | Next trigger |
|---|---|---|---|---|
| **Database write lock in session hours** | a write (an exit, an entry, a heartbeat) waits as long as another writer holds the lock. Observed on the runner Sun night: three single-row writes HELD it 0.44 to 0.65 s (the volume's commit time), one write waited 0.53 s. In session there are more writers (control job every minute, charts and lab every 10, borrow every 15, pilot book every 5) | reads no longer take the lock (journal page, flags, the short book's row selects); stop checks run first; nothing on the event loop waits for it; every wait or hold of 0.25 s and every late stop check is written down with who | one session of data. The first arrives Mon 2026-11-23 20:50 ET (`database:` and `stop checks:` clauses) | a wait of 1 s or more, or any late stop check: the named caller is moved to a read or made shorter, with its test. Five clean sessions close it as measured |
| **An alert received late without a restart** (a specification/code discrepancy, NOT changed) | the registered rule enters at the first bar after alert + 10 s; the live book enters at the print of the moment the alert reaches it, up to five minutes later (median delay 28 s, 90th percentile 53 s). Amendment F5 measured late entries as a worse bet (+0.97% at the alert, +0.14% two minutes late, -0.21% four minutes late) | none, by design: this is the frozen rule for live alerts. Measured every night per trade (`dec_entry`, flag `late`) and separated in the modeled ledger | nothing is missing; it is a rule question | **owner's decision** if a limit on late receipt is wanted (for example: record, not enter, when an alert arrives more than N seconds late). Not prepared, not adopted |
| **Telegram behaviour not yet seen live** | a post Telegram never delivers or deletes before the listener is back is not recovered; a real backlog over 100 and a "slow down" answer during a catch-up have not happened | the nightly check against the channel's own record names an alert that never reached the book; the paging check of row 7; the catch-up obeys "slow down" and keeps the cursor | a real restart with posts in it | the first such restart: checked by query the same day and reported |
| **The hourly research catch-up writes its own database on the event loop** | a wait on that database (not the trading one) would hold back the scheduling of the next pass; passes already running are not affected | the two books' passes are on their own threads; a resulting late stop check is now recorded | whether it ever waits: unknown | a late stop check at about the top of an hour: move that write off the loop |
| **A crash between a row's commit and its decision line** | the line is missing for that row | the nightly line says "alert rows have no line" | none | first occurrence: decide whether to write the line on the next start |
| **Market data outage** | no quote, so no live entry and no live exit until it returns | the official result is computed from the tape; the 09:20 check texts when data is dead before the open; a late stop check is recorded | none | a `stop checks:` warning with a slow pass |
| **Paper record against executable behaviour** | results of the paper books are not results of orders | the nightly modeled ledger and its registered review (first read at 15 / 25 trades); the three unverified daily-book items are due Fri 2026-12-04 | the forward sample | the scheduled reviews; nothing new here |

## 7. Independent reviews used

| Review | Scope | Grade | Findings and what was done |
|---|---|---|---|
| Red-team (operational), 2026-11-23 | the listener's loops and both books' pass, against commit `87b21c8` | Real issues | 5: row 1 (reproduced) fixed; rows 2, 4, 9 fixed; the halt case kept in section 5 |
| Code review of #200 | the recovery change | Sound | 1 low (a failed alert and its gap row in two transactions): fixed in #200 |
| Code review of #201 | first start, threading, legacy rows | Sound | 2 low: fixed in #201 |
| Code review of this change | the diff, asked to break the claim that no decision for a live alert changes | Sound | 5 low, none a blocker. Applied: the watchdog marks itself as having warned only after the text was sent (a failed send is retried the next minute). Left, stated: late stop checks outside alert hours are filed under the UTC day, so one after 19:00 ET shows in the next night's line; a message deleted between a full page and its check costs one repeated pass; at the daily text limit, exit texts are now counted before entry-request texts; the heartbeat is written after the first stop check, so a pass that cannot price many open positions for three minutes trips the watchdog, which is then right |
