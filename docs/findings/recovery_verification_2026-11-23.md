# Listener recovery: post-merge verification (2026-11-22 night to 2026-11-23)

Owner, after merging #200: "Treat the following as post-merge verification, not a request to reopen the merge decision
or expand the project." Six checks, the evidence for each, what was found, and what was changed. Paper only. No
trading rule was changed by this work. The forward cohort and its primary calculation are untouched.

## 0. Status and summary

| | |
|---|---|
| #200 merged | Sun 2026-11-22 23:17:55 ET (main `5be1ae9`) |
| #200 deployed | Sun 2026-11-22 23:24 ET, runner **v108 = main `5be1ae9`**, gate 2,257 passed. Before it: v107 = `e6d0d30` |
| Verified on the runner after the deploy (read-only) | tables `alert_events`, `alert_gaps` present; `fade_trades.via` / `recovered_at` present; cursor set 23:24:07 ET; catch-up state ok; heartbeat 3 s; `fade_off` = 0 and `long_off` = 0 as before; nothing open |

| Check | Result | Follow-up |
|---|---|---|
| 1. Recovered-alert eligibility | **AMBIGUOUS for the short book**; defined for the long book. My statement in #200 that the existing rules define it was too strong for shorts | a record-only safeguard for recovered shorts, prepared as its OWN change for review (a policy decision: not merged or deployed by me) |
| 2. Restart corrections documented | done (section 2): scope, effective version, effect on the cohort's accounting | restart-caused `missed` longs now carry their reason in the decision record |
| 3. First-start initialization | **DEFECT confirmed**: a missing cursor was set at the channel's newest message, so anything posted during that very restart was never read and nothing said so. Tonight's interval is written down in section 3: seven message ids, none tradeable | fixed: the cursor starts after the last message the book has a record of; an interval that is not read gets a gap row |
| 4. Position protection independent | holds for an unreachable channel and a large backlog. **DEFECT confirmed** for a slow replay: its database work ran on the event loop and held back the next stop check | fixed: the replay's work runs on a thread of its own |
| 5. Rollback compatibility | the previous build runs on the new schema and on a half-finished recovery; positions, switches and texts intact. **DEFECT confirmed** on the way back: a filtered alert the previous build handled got a second row | fixed: an alert whose row exists is recognised even without an identifier |
| 6. Controlled recovery test | a real process, killed, alerts posted while it was down, restarted: **12 of 12 checks pass** on the deployed code and on the follow-up | the harness is in the repository (`scripts/recovery_drill.py`) |

None of the three defects produced a trade, a duplicate trade, a wrong price or a lost position, in production or in
any test. Two are record-keeping (an unrecorded skipped interval; a duplicate filtered row after a rollback), one is
a delay of a stop check that exists only while a replay is handling alerts against a slow database.

## 1. Recovered-alert eligibility

**What the specification says.** `docs/findings/prereg_alert_fade_2026-11-01.md`:

| Where | Text | Meaning for the five minutes |
|---|---|---|
| Frozen rule, line 29 | "Entry time and price exactly as in that test" | the entry is ONE bar: the first 1-minute bar at or after alert + 10 s (`scripts/telegram_alert_test.py:85`) |
| `telegram_alert_test.py:86` | `if ... b.ts.iloc[i] - r.posted_at > MAX_ENTRY_DELAY: no_entry` | five minutes bounds how late that FIRST bar may be (a halt, no prints). It is not a window in which any later bar is an entry |
| Live book, line 99 | "10 s reaction, 5 min entry limit ... are the F1 filters" | the same bound, carried to the live book |
| Amendment F3, line 114; F4, line 128 | approvals expire 4 min 20 s after the alert "so that an approved entry still falls inside the tested 5-minute entry window" | here the five minutes WERE read as an eligibility window for a late entry |
| Amendment F5, lines 133 to 135 (owner decision, 2026-11-04) | "the edge is front-loaded (training: +0.97%/trade at the alert, +0.14% two minutes late, -0.21% four minutes late)"; every event "enters at the tested timing (first quote at or after alert + 10 s)" | the late entries of F3/F4 were withdrawn, on evidence that an entry minutes after the alert is a different and worse bet |

**What the code does.** `src/sgt/fast/fade.py:320`: an alert received more than five minutes after its post is
`skipped`, "alert received late" (unchanged since the first live book, commit `42c22c3`, 2026-11-03).
`fade.py:471` (`_enter`): a waiting row enters at the first usable print at or after its entry bar's start, and
gives up when five minutes have passed with none (`fade.py:512`). The code therefore tolerates an alert that
reaches the book late: it enters at the print of that moment. It was written for delivery delay (median 28 s,
90th percentile 53 s on 362 alerts, `chan_poll.py`), and the execution audit measures the cost of that delay
(`dec_entry`, flag `late` in `exec_model_2026-11-21.md`).

**Finding: ambiguous.** The registered rule's five minutes is a bound on the first tradable bar (freshness of the
tape after a halt). The live code also uses it as a tolerance for late receipt, and F3/F4 once used it as an entry
window, which F5 then withdrew on evidence. Nothing in the specification addresses an entry after downtime. So the
code permits a short on a recovered alert up to five minutes old; the specification does not authorize it, and the
one piece of evidence on late entries (F5) argues against it. In #200 I wrote that the existing rules "do define"
eligibility: for the short book that was wrong, and is corrected here and in `listener_recovery_2026-11-22.md`.

**The long book is defined.** `src/sgt/fast/long_book.py:66-69` (2026-11-10, written after a deploy booked a fill
that was an hour old): "A fill whose bar started longer ago than this was not seen live (restart, outage): recorded
as 'missed', never traded or texted." `long_entry_timing_2026-11-20.md`, line 86, names "restart, feed outage" again.
The long rule keys on the SETUP's time, read from current candles, not on when the alert was received: a setup that
completes after the listener is back is traded live, one that completed while it was away is `missed`. #200 made
that stricter (section 2), not looser.

**What runs now (v108).** A recovered short alert up to five minutes old can enter, on a print from after the
recovery only. It needs a restart inside the session within five minutes after an alert; deploys are not done in
session, so it needs a crash. It has not happened.

**Prepared for review, not merged: record-only for recovered shorts** (its own pull request). A recovered alert the
short rules would otherwise let wait is `skipped`, reason "recovered after a restart: recorded, not entered". It
stays an event (the 7-day repeat rule counts it; the long book watches it); nothing else changes; alerts handled
live are untouched. Merging it is the owner's decision on the trading rule; not merging it leaves the code's present
reading in force.

**If the record-only change is merged** (`fade.RECOVERED_SHORT_ENTRIES = False`, this pull request): from the
deploy that follows, an alert found by the replay is never a short entry. Older than five minutes it is still "alert
received late"; a rule's own filter (entry window, news, 7-day repeat) still names it; otherwise it is `skipped`,
"recovered after a restart: recorded, not entered". It stays an event for the repeat rule and for the long book.
The nightly clause counts it ("N recorded only"). An alert handled live, and one found by the 15-second check after
the listener is back, are untouched. The drill in this mode: 12 of 12 (checks 7, 8 and 12 become "with a fresh print
after the recovery it is still not entered, no text", "posting and recovery time stored", "still recorded only
after two crashes"). Effect on the cohort: it can only remove a restart-case trade, and each one is on the record
with its reason. Turning entries back on is a one-word change and, like this one, a decision about the trading rule.

## 2. The restart corrections of #200: scope, effective version, cohort accounting

Both change what the books do after a restart. Neither changes anything for an alert handled live (test
`test_an_alert_handled_live_enters_exactly_as_before`).

| | Short: the entry window is not stretched by downtime | Long: a fill from before the listener was back is missed |
|---|---|---|
| Before (through v107) | a row waiting to enter when the listener stopped was entered on the first print after it came back, however late; and on a print that could be minutes old | a fill up to five minutes old was booked at its old price after a restart |
| From v108 | no print from before the listener could act is used (`fade._floor`); a row whose five minutes passed while the listener was away is `skipped`, reason "no trade print after the restart within 5 min of the alert" | a fill whose candle began before the recovery / the listener's start is `missed` (`long_book.live_from`) |
| Applies when | the alert was recovered (`recovered_at` set), or the listener restarted while the row waited | the alert was recovered, or the listener restarted during the alert's watch window |
| Effective | runner v108 = main `5be1ae9`, Sun 2026-11-22 23:24 ET: before the forward cohort's first session (Mon 2026-11-23), so the WHOLE cohort runs under it | same |
| Did the old behaviour ever fire? | no: of the 29 shorts ever entered none was entered more than 5.5 minutes after its post (longest 266 s; runner, read-only, 2026-11-22) | not since 2026-11-11: all 23 longs from then to 11-20 were booked 62 to 88 s after their fill minute, the normal detection delay (`long_book.py`, note on `AT_CLOSE`). The hour-old KULR fill of 2026-11-10 is what created `missed` |
| Effect on the cohort's accounting | such a row is an event WITHOUT a trade: it is not in the priced sample (only settled trades are), it still counts for the repeat rule and as a long event. Identifiable by its reason | `missed` was never a trade and is never scored officially: not in the sample. Identifiable: the decision line carries `missed_why` (from the follow-up) and `via` / `recovered_at` |
| Direction | fewer trades, only in a restart case; never a trade that the old code would not have made | same |

The registered review reads settled trades with no exclusion flag from 2026-11-23, as written. These corrections can
only REMOVE a restart-case trade from that population, and each removal is on the record with its reason. Any
"with and without recovery" figure is a diagnostic beside the registered calculation, never a replacement for it.

## 3. First-start initialization

**What #200 did.** With no cursor, `ordered_pass` set it at the channel's newest message and replayed nothing. Two
ways an alert could be dropped without a record: (a) posted while the old process was stopping and the new one
starting; (b) pushed while the cursor was being set (the push path is closed until the catch-up finishes, and the
cursor then landed at or past that message).

**Tonight's first start, written down.** The deploy of #200 was that first start.

| | |
|---|---|
| last message the book had a row for | 27256, posted Fri 2026-11-20 16:24:11 ET |
| cursor set at | 27263, Sun 2026-11-22 23:24:07 ET (audit event `alert_cursor_baseline`) |
| interval not read by the new build | message ids 27257 to 27263 |
| what the channel's own record shows in it | 4 parsed alerts, all Friday after the close (16:25 to 17:38 ET): one news item and three after-hours highs. Ids 27261 to 27263 hold no parsed alert as of 23:24 ET |
| consequence | none: the previous build was running when the four were posted and handled them (kinds and times the book does not act on leave no row), and nothing in the interval is a tested kind inside a session |

**Fixed.** A missing (or unreadable) cursor now starts after the LAST message the book has any record of, and
everything after it is read and handled, in order, labelled `replay` where it was posted before the listener came
up. Only a book with no record at all starts at the newest message, and then the interval it does not read is
written to `alert_gaps` like every other stretch the listener decides not to handle. Tests:
`test_the_first_start_resumes_after_the_last_message_on_record_so_nothing_posted_meanwhile_is_dropped`,
`test_a_book_with_no_record_starts_at_the_newest_message_and_writes_down_what_it_did_not_read`,
`test_an_unreadable_cursor_with_alerts_on_record_resumes_after_the_last_of_them`.

On the runner the cursor now exists, so this path will not run again there unless the cursor is lost.

## 4. Position protection is independent of the alert channel

The stop checks run in their own loops (`scripts/fade_live.py`: short book every 10 s, long book every 15 s, tape
check every 30 s), each on a worker thread. They share one thing with the alert path: the event loop that schedules
them. Tests, each with an open short, its monitor loop running as the listener runs it, and a catch-up under way
when the price goes through the stop:

| Situation | Test | Deployed #200 | With the follow-up |
|---|---|---|---|
| the channel never answers | `test_an_alert_channel_that_never_answers_does_not_hold_an_exit` | pass | pass: exit booked in under 0.5 s |
| backlog of 200 messages | `test_a_large_backlog_does_not_hold_an_exit` | pass | pass |
| slow replay (each alert takes 1 s, as when the database is busy) | `test_a_slow_replay_does_not_hold_an_exit` | **FAIL**: the exit waited for the replay | pass |
| the channel raises on every request, in a real process | drill, check 9 | pass | pass |

The defect: #200's replay handled each alert on the event loop. A database write that waits (the open risk of
`ops_risks_2026-11-22.md`) then held back the scheduling of the next stop check for as long. Fixed: everything the
ordered pass does with the database runs on one thread of its own (`alert_recovery._off_loop`), not on the event
loop and not on the worker pool the monitors use.

Not changed, stated: an alert that arrives by PUSH is still handled on the event loop, as it always was (before
#200 as well). One alert is one small write; it is bounded by the same database contention that stays an open risk.
Moving it would change the live path, which this work does not touch.

## 5. Rollback compatibility

Run for real: one database file handled by the new build, then by the previous build (`e6d0d30`, from its own
checkout), then by the new build again (`scratchpad/rollback/phases.py`; each phase its own process).

State handed to the previous build: an open short, an open long, a switch set by the owner (`long_off`), a cursor
in the middle of a recovery, three alert identifiers, two recovered alerts waiting to enter, one alert of a message
not yet on record.

| Question | Result |
|---|---|
| Does the previous build start on the new schema? | yes: two extra tables and two extra columns are ignored |
| Positions | the open short was closed at its stop by the previous build (one exit text); the open long was still open; nothing lost |
| Switches | `long_off` and the day's arming unchanged through all three phases |
| New alerts under the previous build | handled as before (rows without identifiers) |
| The nightly line and the site's alert list under the previous build | both run |
| Duplicate actions under the previous build | none: 4 texts for 4 events (1 exit, 3 entries) |
| The two recovered alerts that were waiting | the previous build entered them by ITS rules: it has none of the restart corrections of section 2. A rollback restores the old restart behaviour for as long as it lasts |
| Roll forward again: duplicate entries or texts | none (0 entries booked; the 3 texts were the due 60-minute exits) |
| Roll forward again: duplicate rows | **deployed #200: one** (a filtered alert the previous build had handled got a second filtered row: no trade, a doubled record). **Follow-up: none** |

Fixed: when an alert's own row already exists without an identifier (written by a build without identifiers), the
alert is recognised as handled, its identifier is written, and nothing else is (test
`test_an_alert_whose_row_was_written_by_a_build_without_identifiers_never_gets_a_second_row`).

Rule for a rollback, unchanged: only through the deploy script, which refuses while any alert row is waiting or
open. With nothing waiting there is no recovered alert for the previous build to enter by its own rules.

## 6. Controlled recovery test

`scripts/recovery_drill.py run`. A listener process built from the live listener's own parts (the channel's real
message format and parser, the ordered catch-up, the push path, the short book's alert handling and monitor) runs
against a channel that is a file, a price tape that is a file and an empty database in a temporary directory. The
drill kills it with SIGKILL, posts alerts while it is down, starts it again. Texts go to a file. No network, no
broker, no phone, not the live database. The clock runs 12 times faster; every rule reads the drill's clock.

| # | Check | Deployed `5be1ae9` | Follow-up |
|---|---|---|---|
| 1 | live alert: handled by push, entered at the current print, one text | pass | pass |
| 2 | ordering: the downtime's messages handled oldest first | pass | pass |
| 3 | replay label: `via = replay`, posting time, a later recovery time, on every recovered alert | pass | pass |
| 4 | expiration: the alert older than five minutes is recorded, not entered | pass | pass |
| 5 | one trade per stock survives the crash: a second alert of the shorted stock is a duplicate, no row | pass | pass |
| 6 | current price: a fresh recovered alert is NOT entered on the print from the downtime | pass | pass |
| 7 | current price: it enters on the first print AFTER the recovery, at that price, one text | pass | pass |
| 8 | both timelines kept: posting, recovery and entry are three stored times | pass | pass |
| 9 | position protection: stop exit booked and texted while the channel is unreachable | pass | pass |
| 10 | expiration: a recovered alert whose stock never prints inside five minutes is skipped | pass | pass |
| 11 | deduplication: after a second kill with the cursor wound back, and the same message pushed again: no new row, identifier, decision line or text | pass | pass |
| 12 | position state survives two crashes | pass | pass |

Not exercised by the drill: Telegram's own delivery (how the library pages a channel was read from its source; the
first production proof is the first deploy after the cursor exists: a post during that restart must appear as
`replay`), and the long book (its restart rule has five deterministic cases in the tests). Checks 7 and 12 describe
the code's present reading of eligibility; under the record-only safeguard of section 1 the drill checks instead
that the fresh recovered alert is recorded and never entered.

## 7. Follow-up changes

| Change | Kind | Status |
|---|---|---|
| first start anchored on the last message on record; unread interval written down | engineering fix (check 3) | this pull request |
| the replay's database work off the event loop | engineering fix (check 4) | this pull request |
| an alert whose row exists without an identifier is recognised | engineering fix (check 5) | this pull request |
| `missed_why` on the decision line of a missed long | record (check 2) | this pull request |
| the drill harness | test tool (check 6) | this pull request |
| record-only for recovered shorts | TRADING RULE (check 1) | its own pull request, for the owner's review |

## 8. Remaining operational risks

| Risk | Status |
|---|---|
| The database's write lock in session hours | OPEN. Measured only on a quiet Sunday. The recording of #198 runs from v107; the first session's numbers arrive Mon 2026-11-23 at 20:50 ET |
| An alert pushed live is handled on the event loop | unchanged from before #200; bounded by the lock above |
| A post Telegram never delivers, or deletes before the listener is back | cannot be recovered; the nightly check against the channel's own record names it |
| An alert received late WITHOUT a restart | unchanged: the existing late rule, no print-after-receipt floor (that would change the rule for live alerts). F5's evidence on late entries applies to it too; it is measured nightly (`dec_entry`), not changed |
| Telegram's paging in production | read from the library's source, not yet observed live with a backlog; first proof at the next deploy |
| A rollback while a recovered alert waits | prevented by the deploy script (refuses while a row waits or is open); done by hand it would restore the old restart behaviour |
| A stop resting at a broker | does not exist (paper); the only real protection; deployment blocker, unchanged |
