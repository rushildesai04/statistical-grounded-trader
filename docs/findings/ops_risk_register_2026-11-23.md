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

> **Second pass, 2026-11-23 01:30 to 02:30 ET (owner: "Tighten risk-register claims").** Changed in place below and
> marked "(second pass)": the disk is no longer listed as disproven; the limits of the status guard are stated;
> missing or late texts are an open limitation, not "not applicable"; the hourly research catch-up is fixed, not
> open; stop-check intervals are measured, not assumed; the late-receipt discrepancy has its requirement and its
> counts; which checks are durable and which are not.

## 1. Running version and what was verified

| | |
|---|---|
| Running (second pass) | runner **v110 = main `d603298`** (#203, including its last fix), deployed Mon 2026-11-23 01:34 ET, gate passed; verified read-only: heartbeat 3 s, both books on, catch-up ok after the restart, cursor intact, nothing open, no watchdog text. Before it: v109 = `87b21c8`, Sun 23:59 ET |
| Merged in it | #201 (first start after the last record, replay off the event loop, rows without identifiers recognised, `missed_why`, the drill) and #202 (recovered shorts record-only). Diff `5be1ae9..87b21c8` on the trading path: the record-only rule, the legacy-row guard, a local helper with the same reason strings, `missed_why`. Nothing else |
| Targeted checks on `87b21c8` | 163 tests of the alert, short, long, decision and lock paths pass; the recovery drill passes 12 of 12 in record-only mode |
| Recovered shorts | recorded with `via = replay`, posting and recovery time; never entered, also with a fresh print after the recovery (tests, drill check 7). Reason "recovered after a restart: recorded, not entered" |
| Repeat rule for a recovered alert | unchanged definition (`fade.prior_alert`): an operational skip is an event, as "alert received late" and "paused" are; a row a rule filtered keeps that rule's reason; only "no print" skips are not events. Tested: the recovered alert counts as a prior alert the next day, and the long book still watches it |
| First production restart with a cursor (v109, 23:59 ET) | the catch-up ran from cursor 27263, reached the newest message, wrote no gap and no duplicate; `alert_catchup` ok at 23:59:27 ET. **Nothing was posted during the restart, so no alert was recovered.** Verified: start-up with an existing cursor, the push gate opening, state intact. NOT yet observed in production: a real post recovered as `replay`; a backlog over 100 messages; Telegram's "slow down" answer during a catch-up |
| First start of #200 (v108), restated with the channel's own record | cursor set at 27263; ids 27257 to 27263 were posted Fri 16:25 ET to Sat 23:50 ET, all handled by the build then running; the newest stored message was 27263, so nothing was posted during that restart either |

## 2. Effective versions (for the cohort's accounting)

Every version below through v110 went live BEFORE the forward cohort's first session (Mon 2026-11-23 09:30 ET): v110
was deployed Mon 01:34 ET, so every decision line of the cohort carries the full label. A later engineering deploy
gets its own row here. The cohort is ONE cohort across them: it is not restarted, and no trade is excluded, because
an engineering version changed; a row of this table is how a reader tells which build made a decision.

| Runner | main | Live from (ET) | What changed that the accounting needs to know |
|---|---|---|---|
| v107 | `e6d0d30` | Sun 11-22 23:09 | reads without the write lock (journal page); lock waits recorded |
| v108 | `5be1ae9` | Sun 11-22 23:24 | recovery: durable cursor and identifiers; replay label; **restart corrections**: a short's entry window is not stretched by downtime, no entry on a print from before the listener was back, a long fill on a candle that began before then is `missed` |
| v109 | `87b21c8` | Sun 11-22 23:59 | **recovered shorts record-only**; first start after the last record; replay off the event loop; `missed_why` on a missed long's decision line |
| v110 | `d603298` | Mon 11-23 01:34 | **rule-version labels name the restart rules** (the labels of v108 and v109 did not: a record gap found in this review); stop checks first in every pass; alert handling off the event loop; late stop checks recorded; the watchdog text |
| v111 | `9a4cc65` (#204) | merged Mon 11-23 10:52 ET in session; deploys after the close (`ops_followups_2026-11-23.md`, section 1) | no rule, no label change. The research catch-up off the event loop; stop-check intervals measured; the watchdog reads three signs of life; the 09:20 check covers the alert intake |

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

| 11 (second pass) | **The hourly research catch-up did database work on the event loop.** `TelegramCollector._fetch` writes each message as it arrives, on the loop, inside one session held open across the network reads; the research database waits up to 30 s for a writer | the scheduling of every loop's next pass (stop checks included), alert receipt by push | DEMONSTRATED (controlled slow write: storing one message takes 0.4 s; every other task on the loop stood still for 0.35 s or more) | `backfill_off_loop`: the start id is read and the messages stored on worker threads, in batches of their own; the fetch stays on the loop. And `watch_loop` writes down any time the event loop stands still for 3 s, whatever the cause | `test_a_slow_database_write_in_the_catch_up_stops_the_event_loop_only_on_the_old_path` (asserts the stall on the old path AND its absence on the new one), `test_an_event_loop_that_stands_still_is_written_down...` |

Also from this review, a diagnosis aid: a slow database wait now names the code that asked for the write, not the
store helper (`... via set_flag`).

## 4. Disproven or closed, with the evidence

| Claim challenged | Result |
|---|---|
| Two processes (the listener, a cron job, the tape check) can close or enter the same row twice | CANNOT OCCUR: every state change is `UPDATE ... WHERE id=? AND status=?` inside the single write lock; the loser changes nothing. Traced by two independent reviewers; exercised in the rollback run (two builds on one database). **What this guard does NOT cover (second pass):** the side effect after the state change. The row is committed, THEN its text is sent. A death in between leaves the state right and the text unsent; it is never sent twice, and never sent later. Tested both ways (`test_a_death_between_an_exit_and_its_text_never_doubles_the_exit_but_the_text_is_lost`). See section 6 |
| A second delivery of an alert (push and check, replay, retry) duplicates a row, decision line, entry or text | CANNOT OCCUR: the durable identifier is checked inside the write. Tests; drill check 11 (kill, cursor wound back, same message pushed again) |
| A stop-check loop can die quietly while the process lives | CANNOT OCCUR: each loop body is wrapped whole; a lost Telegram connection ends the process, which the supervisor restarts (red-team, verified). A hung process is now caught by row 5 |
| A stop check can wait for a worker thread | closed by row 4 |
| The decision record can block or be lost silently | closed in #197: 0.5 s bound, spool, nightly replay and health line; tests for a locked database, a missing table, a dead disk |
| A rollback corrupts state or duplicates actions | closed in #201 (run on the real previous build; a duplicate filtered row fixed); rollback only through the deploy script, which refuses while a row waits or is open |

## 5. Not applicable while paper-only: requirements for real orders (kept, not closed)

| Requirement | Why it is not a defect of the paper book |
|---|---|
| A stop resting at the broker | paper positions have no broker; every stop here is a check by this process. For real money this process must never be the only protection |
| The stop when a stock is halted or silent on the free feed: `fade._exit` has no price, so it marks the trade at its last print and waits (red-team) | the OFFICIAL result is computed from the full tape by the research rule whatever the live estimate did; the live text can be late by up to 16 minutes (the tape check). Real money needs real-time full-tape data and a broker stop |
| An order sent and its record must survive a crash together | paper has no order. Real orders need idempotency keys at the broker and a reconciliation on start. (The paper-side effect of the same window, a lost text, is a limitation NOW: section 6) |
| Borrow, locate cost, the short-sale price test, queue position, partial fills | the nightly modeled ledger prices them (a research ledger, not stop protection); H1 is registered for the price-test case |
| Durability against power loss versus commit speed (`synchronous`) | left at the durable setting; changing it trades lost commits for speed and is not needed on the evidence so far |

## 6. Open

| Risk | Concrete impact | Safeguard now | Evidence or capability missing | Next trigger |
|---|---|---|---|---|
| **Database write lock in session hours** | a write (an exit, an entry, a heartbeat) waits as long as another writer holds the lock. Observed on the runner Sun night: three single-row writes HELD it 0.44 to 0.65 s (the volume's commit time), one write waited 0.53 s. In session there are more writers (control job every minute, charts and lab every 10, borrow every 15, pilot book every 5) | reads no longer take the lock (journal page, flags, the short book's row selects); stop checks run first; nothing on the event loop waits for it; every wait or hold of 0.25 s and every late stop check is written down with who | one session of data. The first arrives Mon 2026-11-23 20:50 ET (`database:` and `stop checks:` clauses) | a wait of 1 s or more, or any late stop check: the named caller is moved to a read or made shorter, with its test. Five clean sessions close it as measured |
| **An alert received late without a restart** (a specification/code discrepancy, NOT changed; second pass: the requirement and the counts) | **The requirement:** `prereg_alert_fade_2026-11-01.md`, frozen rule, "Entry time and price exactly as in that test": the first 1-minute bar at or after alert + 10 s (`telegram_alert_test.py:85`), and Amendment F5, "every qualifying event enters at the tested timing (first quote at or after alert + 10 s)". **The implementation:** `fade._enter` enters at the first usable print after the alert REACHED the book, which can be after that bar began. **Counts (history to 2026-11-20, before the cohort; counts only, no result was read):** of 29 shorts ever entered, 9 reached the book after their tested entry minute had begun; 2 were entered more than 60 s after it began (the longest 208 s, a stock with no earlier print); receipt delay median 33 s, 90th percentile 58 s, longest 59 s. Of all 422 alert rows, 3 arrived more than 60 s after posting, none more than 120 s, none was "received late" (over 5 minutes) | none, by design: this is the frozen behaviour for live alerts. Each trade's flag `late` and its price from the bot's own decision time are in the nightly ledger | nothing is missing; it is a rule question | **owner's decision**, to be taken on the specification and the pre-cohort evidence (F5), NOT on the cohort's interim returns. Nothing is prepared or adopted; the cohort's entry timing is unchanged |
| **Telegram behaviour not yet seen live** | a post Telegram never delivers or deletes before the listener is back is not recovered; a real backlog over 100 and a "slow down" answer during a catch-up have not happened | the nightly check against the channel's own record names an alert that never reached the book; the paging check of row 7; the catch-up obeys "slow down" and keeps the cursor | a real restart with posts in it | the first such restart: checked by query the same day and reported |
| **The disk fills (second pass: MITIGATED, low priority; not disproven)** | every write fails: no entry or exit can be booked, no text for an unbooked exit; the listener keeps running and logs each pass | measured Mon 01:35 ET: 2.10 of 5.18 GB used; 1.80 GB of it is the market-data cache, which nothing prunes; at most 91 MB was written in the last 7 days (about 30 weeks of room at that rate). The 09:20 check texts under 10% free. Failure behaviour tested (`test_a_full_disk_books_nothing_texts_nothing_corrupts_nothing...`): nothing booked, nothing texted, the row intact, the heartbeat stops (so the watchdog texts within 3 minutes), and the exit is booked once when writing works again | no retention rule for the market-data cache | the 10% text, or 4 GB used: prune the cache by age |
| **A text that is late, or never sent (second pass)** | the owner's picture of the day is wrong until the nightly summary; and a long's official price "when its text went out" is measured from the text's time. Three ways: the text service fails (recorded, shown nightly); the day's text limit is reached (one notice, then recorded); the process dies between a state change and its text (the text is lost and NOTHING records it) | every trade is on the site and in the nightly summary; a failed or dropped text is counted; a restart in alert hours is now written down and warned about, which is the moment such a text could have been lost | a per-trade record that its text left (none exists) | a restart warning in the nightly line: the trades changed in the minute before the silence are checked against the texts sent, by hand. If it ever happens: write the text into the same transaction as the state change and send from there |
| **A machine that is down (second pass)** | everything stops, and so does every check that runs on it: the watchdog, the 09:20 check, the nightly line | the platform is expected to restart the machine (not verified here); nothing tells the owner | a check from OUTSIDE the machine (none exists) | **owner's decision**: the smallest option needs no reachable address: a dead-man's switch at an outside service, prepared and inactive (`ops_followups_2026-11-23.md`, section 3) |
| **A crash between a row's commit and its decision line** | the line is missing for that row | the nightly line says "alert rows have no line" | none | first occurrence: decide whether to write the line on the next start |
| **Market data outage** | no quote, so no live entry and no live exit until it returns | the official result is computed from the tape; the 09:20 check texts when data is dead before the open; a late stop check is recorded | none | a `stop checks:` warning with a slow pass |
| **Paper record against executable behaviour** | results of the paper books are not results of orders | the nightly modeled ledger and its registered review (first read at 15 / 25 trades); the three unverified daily-book items are due Fri 2026-12-04 | the forward sample | the scheduled reviews; nothing new here |

### Stop checks: intended against observed (second pass)

| | |
|---|---|
| Intended | the short book checks every open position every 10 s, the long book every 15 s |
| What can stretch it | a pass that takes long (slow quotes; before v110 also waiting alerts' lookups), an event loop that stands still, a busy database, a thread that waits |
| Measured before v111 | nothing: only checks more than 25 s / 35 s late were written down (v110), and none was by 01:35 ET, a minute after it started (idle: no position open) |
| Measured from v111 | every interval between two stop checks and the time each check took, per loop: counted within 1.1 x the intended interval, within 1.5 x, within the late limit, later; longest gap; longest single check. Written every 10 minutes; the nightly line reports alert hours: "short book N checks, x% within 11 s, longest gap G s, longest single check W s" |
| First real numbers | the first session after v111 is deployed, at 20:50 ET. Idle hours say nothing about a session and are not offered as evidence |
| What this does NOT establish | that a stop crossing is DETECTED in time. The check reads the free feed, which carries under 1% of the volume: the price it sees can be old or absent while the stock trades elsewhere (a halted or thin stock has no price at all; section 5). A check every 10 s on that feed is a check of that feed. The official result comes from the full tape afterwards |

### Which checks are durable (second pass)

| Check | Runs where | Depends on a chat session? |
|---|---|---|
| Watchdog, every minute in alert hours: three signs of life (short book's stop check, long book's stop check, the reading of the channel), none of them "an alert arrived". Tested against a real process frozen with SIGSTOP | the runner's own schedule, a process separate from the listener | no |
| 09:20 ET pre-open check: heartbeat, market data, database writable, disk; from v111 also the alert intake (channel read in the last 15 minutes, catch-up finished, no stretch passed unhandled since the close) | the runner's own schedule | no |
| Nightly health line, 20:50 ET | the runner's own schedule | no |
| My read-only look at the listener at 09:07 ET on 2026-11-23 | a timer inside the assistant's session | **YES: it runs only if that session is still open. It is an extra look, not monitoring, and nothing depends on it** |
| Anything from outside the machine | does not exist | see "A machine that is down" above |

## 7. Independent reviews used

| Review | Scope | Grade | Findings and what was done |
|---|---|---|---|
| Red-team (operational), 2026-11-23 | the listener's loops and both books' pass, against commit `87b21c8` | Real issues | 5: row 1 (reproduced) fixed; rows 2, 4, 9 fixed; the halt case kept in section 5 |
| Code review of #200 | the recovery change | Sound | 1 low (a failed alert and its gap row in two transactions): fixed in #200 |
| Code review of #201 | first start, threading, legacy rows | Sound | 2 low: fixed in #201 |
| Code review of this change | the diff, asked to break the claim that no decision for a live alert changes | Sound | 5 low, none a blocker. Applied: the watchdog marks itself as having warned only after the text was sent (a failed send is retried the next minute). Left, stated: late stop checks outside alert hours are filed under the UTC day, so one after 19:00 ET shows in the next night's line; a message deleted between a full page and its check costs one repeated pass; at the daily text limit, exit texts are now counted before entry-request texts; the heartbeat is written after the first stop check, so a pass that cannot price many open positions for three minutes trips the watchdog, which is then right |
| Code review of the second-pass change | the catch-up off the loop, the interval measurement, the watchdog, the pre-open check | Minor gaps | 1 medium, 3 low, all applied: the watchdog said "Alerts are not being read" when only the short book's thread was stuck (it now says what the channel's own sign shows); a Telegram "slow down" pause reads as an intake fault (wording now names both causes); the pre-open check's 20-hour window missed Friday-evening gaps on Monday (now: since the previous pre-open check); a full record of stop checks stopped silently (the nightly line now says so) |
