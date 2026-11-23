# Listener restart recovery: built (2026-11-22)

Owner, 2026-11-22: "Build listener restart recovery now as a separate PR. Do not wait for another missed alert."
The design was in `ops_risks_2026-11-22.md`, section 2. This document says what was built, where it differs from that
design and why, how each of the eight requirements is met and which test shows it, what is NOT covered, and how it is
deployed. All trading stays on paper. No entry rule, exit rule, gate or size was added or loosened.

## 0. In one table

| Question | Answer |
|---|---|
| What happens to an alert posted while the listener is down? | When the listener is back it is found, written down with its posting time, its recovery time and the label `replay`, and handed to the same rules as any alert, with the real clock. |
| Can it be traded? | Only if the existing rules still allow it at that moment, on a price that printed AFTER the recovery. Older than five minutes it is "alert received late" (recorded, never entered), as any late alert always was. |
| Can an alert be handled twice? | No. Each alert has a durable identifier written in the same transaction as its row. A second delivery finds it and writes nothing. |
| Can the listener skip an alert without saying so? | No. The cursor passes an unhandled message only together with a row in `alert_gaps`, and the nightly line warns. |
| What changed for an alert handled live? | Nothing in what the books do. One extra small write per message (the cursor) and one row per alert in the new identifier table. |
| First start after this deploy | Sets the cursor at the channel's newest message. Nothing older is replayed. Recovery works from the second start on. |

## 1. How it works

**One ordered path.** `alert_recovery.ordered_pass` asks the channel for every message after the durable cursor,
oldest first, hands each to the book and moves the cursor one message at a time. At start-up that pass is the replay.
While running it is the 15-second check that `chan_poll.poll_once` used to be (every 5 minutes outside alert hours, so
the cursor keeps up with posts before the open and after the close).

**Durable state** (`sgt.ops.alert_log`, three pieces in the ops database):

| Piece | What it holds | Rule |
|---|---|---|
| `alert_events` | one row per alert of a message: key (channel, message id, symbol, alert kind), posting time, time received, `via` (`push`, `poll`, `replay`), recovery time, what the book did first, the row it made | written in the SAME transaction as the alert's own row (`fade._insert`): both are stored or neither |
| cursor, flag `alert_cursor:<channel>` | the highest message id up to which every alert of every message is on record | `alert_log.advance` checks that in the transaction that moves it, and never moves it backwards |
| `alert_gaps` | every stretch of the channel the listener did NOT handle, with the reason | written in the transaction that moves the cursor past it: the only way past an unhandled message |

**Order before speed, at start-up only.** The push path stays the fast path, but it only dispatches once the ordered
pass has reached the newest message. Until then a pushed message waits for the pass, which reaches it in its place.
Reason: an earlier alert of a stock decides whether a later one is a duplicate or a repeat, so a live alert must
never be handled before an older, still unprocessed one. In steady state the push path is exactly what it was.

**The label.** An alert is `replay` when it was posted before this listener began listening (`up_since`, taken the
moment the Telegram connection is up). Its row, its identifier and its decision line carry the original posting
time, the recovery time and the age at recovery. The site's alert list shows "[recovered after a restart, N s after
the post]"; the nightly ledger row carries `via` and `recovered_at`.

## 2. Recovery is not trade eligibility

Recovery decides one thing: that the alert is on the record. Whether it trades is decided by the rules that already
existed, applied with the facts of the moment of recovery. Nothing here adds a way to enter.

| Age when recovered | Short book | Long book |
|---|---|---|
| over 5 minutes | `skipped`, "alert received late": the rule `fade.on_alert` has always applied to an alert received late for any reason. Recorded, still an event for the 7-day repeat rule, never entered | watched for whatever is left of its window, on current candles; see below |
| up to 5 minutes | passes or fails the receipt rules like any alert (entry window, news alert, 7-day repeat, pause, limits, arming). If it passes it WAITS; it enters only when every at-entry check passes at that moment (company news, beta, price $1 to $50, borrow, the off switch), a trade PRINTS AT OR AFTER THE RECOVERY, and the 5 minutes since the post are not over. Otherwise it is skipped | same |

Five minutes is therefore the existing late rule doing its usual job, not an authorization: a recovered alert of two
minutes whose stock does not print again inside the window is `skipped`, reason "no trade print after the restart
within 5 min of the alert".

Three things were made STRICTER, all only when a restart is involved:

1. **A short entry never uses a print older than the moment the listener could act** (`fade._floor`): the later of
   the tested entry bar's start, the alert's recovery time and the listener's start. Before, the entry took the
   latest print since the bar's start, which after a restart can be minutes old.
2. **A short that waited through its five minutes while the listener was down is skipped, not entered late.**
   This was an existing defect found while building: the five-minute limit was only enforced when no print was
   found, so a row left waiting by a crash was entered on the first print seen after the restart, however late.
   Checked on the runner, read-only, 2026-11-22 23:09 ET: of the 29 shorts ever entered, none was entered more than
   5.5 minutes after its post (the longest took 266 s). The defect never produced a trade.
3. **A long fill on a candle that began before the listener was back is `missed`** (`long_book.live_from`), the
   status the book already used for a fill more than five minutes old. Before, a fill up to five minutes old was
   booked at its old price after a restart.

For an alert handled live, both marks lie before the alert, so `_floor` returns the bar's start and `live_from`
changes nothing (test `test_an_alert_handled_live_enters_exactly_as_before`).

**Owner's call, stated plainly.** The brief says: if the existing rules do not define recovery eligibility, record
without entering. They do define it for both books (the late rule and the entry window for the short; the watch
window and `missed` for the long), so a recovered alert that is still fresh can become a paper trade, at a price
from after the recovery. If recovered alerts should be record-only regardless, that is a three-line change in
`fade.on_alert` and `long_book.tick`; it is not built, because it would be a new rule.

## 3. The eight requirements

| # | Requirement | How | Test (`tests/fast/test_alert_recovery.py`) |
|---|---|---|---|
| 1 | posting time, recovery time, replay label persisted | `alert_events` and `fade_trades` (`via`, `recovered_at`), decision line facts | `..._recorded_with_both_times_and_a_replay_label` |
| 2 | recovery separate from eligibility | section 2 | `test_recovery_is_not_eligibility...`, `test_a_fresh_recovered_alert_enters_only_on_a_print_after_the_recovery...` |
| 3 | never a historical price; both timelines kept | `_floor`, `live_from`; `posted_at`, `recovered_at`, `est_entry_at` | the two above, `test_no_print_after_the_recovery...`, `test_a_quote_that_cannot_prove_its_time...`, `test_a_long_fill_from_before_the_listener_was_back...` (5 cases) |
| 4 | crash before / after persistence | row and identifier in one transaction; at-least-once delivery made exactly-once by the identifier | `test_the_row_and_its_identifier_are_stored_together_or_not_at_all`, `test_a_crash_before_the_alert_was_stored...`, `test_a_crash_after_the_alert_was_stored_but_before_the_cursor_moved...` |
| 4 | duplicate delivery | identifier checked before and inside the write, and the table's key | `test_one_alert_is_handled_once...`, `test_two_writers_racing...`, `test_push_and_the_ordered_pass_deliver_the_same_message...` |
| 4 | partial replay, several symbols in one message, cursor only after every one | `alert_log.advance` | `test_the_cursor_moves_only_when_every_alert...`, `test_a_message_with_several_stocks...` |
| 4 | pagination | pages of 100, oldest first, 200 a pass, then the next pass | `test_a_long_backlog_is_read_in_pages...` |
| 4 | missing / deleted messages, cursor gaps | ids are only compared, never assumed contiguous; an unreadable cursor is never read as 0 | `test_deleted_messages_and_holes...`, `test_an_unreadable_cursor_is_never_read_as_zero` |
| 5 | idempotent at the decision level | identifier before any row or decision line; `long_trades.fade_id` unique; every state change has a status precondition; texts follow state changes | the crash-after test (one entry, one text, one decision line across two replays) |
| 6 | switches, one trade per stock, limits, positions survive | all of it is read from the database on every decision; nothing about eligibility lives in memory | `test_switches_limits_and_the_one_trade_per_stock_rule_hold...`, `test_an_open_short_is_still_managed_after_a_restart`, the two waiting-short tests |
| 7 | incomplete or over-bound replay reported, safe behaviour | section 4 | `test_a_backlog_too_old_to_matter...`, `test_an_unreadable_message...`, `test_an_alert_that_keeps_failing...`, `test_an_unreachable_channel_at_start...` |
| 8 | cohort definition unchanged, stages distinguishable | section 5 | `test_recovered_alerts_are_counted_by_stage...`, `test_a_recovered_alerts_trade_is_priced_like_any_other_and_carries_its_label` |

An independent review of the diff (one reviewer, asked to falsify it) graded it Sound and found one low-severity
gap: the failed record and its gap row were two transactions, so a crash between them could have lost the nightly
warning (never a trade). Fixed: one transaction, with its own test
(`test_a_failed_alert_and_its_gap_row_are_written_together_or_not_at_all`); failed alerts are now counted however
they arrived.

The tests were checked against deliberately broken code: no entry floor, an ungated push path, a cursor that moves
without the record check, no long-fill floor, a stretched window after a restart and a lost replay label each fail
between 1 and 8 tests.

## 4. Bounds and what happens when they are met

| Situation | Behaviour | Where it shows |
|---|---|---|
| backlog of more than 200 messages | handled 200 a pass, oldest first, pass after pass every 2 s; the push path waits until the newest message is reached. Nothing is skipped | log; nightly line if still behind at 20:50 ET |
| backlog older than 8 days (the repeat rule looks back 7) | NOT handled: it can change no rule. One recorded jump: a row in `alert_gaps` with the first and last message id | ⚠️ nightly line: "1 stretch of the channel NOT handled (message ids a..b: ...)" |
| a message that cannot be read as alerts | passed at once with a gap row (nothing to retry) | ⚠️ nightly line; audit `alert_gap` |
| an alert the book fails on (for example the database is busy) | the cursor stays; the pass stops there and tries again, in order; after 3 passes the alert is written down as `failed` together with its gap row (one transaction) and the channel moves on | ⚠️ "N alerts failed" and the gap; "catch-up NOT complete since HH:MM (stopped at message N)" only if it lasts 10 minutes |
| the channel cannot be reached at start | cursor unchanged; the push path stays closed; retried every 2 s (Telegram's "slow down" is obeyed). An alert that waits longer than five minutes becomes "alert received late" | ⚠️ "catch-up NOT complete since HH:MM (...)" once it has lasted 10 minutes (a single failed check that the next one repairs is not reported) |
| the cursor's stored value cannot be read | never read as 0 (that would be the channel's whole history): restart from the newest message, with a gap row saying so | ⚠️ nightly line |
| crash at any point | the next start resumes from the cursor; anything already stored is recognised by its identifier | nothing to report: no loss, no duplicate |

The safe side everywhere is the same: when in doubt the alert is recorded and not traded, and the cursor does not
move without a record.

## 5. The forward cohort's accounting

The cohort's definition does not change: a recovered alert is an event like any other and its trade, if the rules
allowed one, is a trade of the book, priced by the nightly execution model like any other. The stages are kept
apart so every result can be read with and without them (`alert_recovery.account`):

| Stage | Meaning | Source |
|---|---|---|
| missed | a channel alert of a tested kind that never reached the book | the decision-record clause, against the channel's own record (unchanged) |
| recovered | found by the catch-up, posted before the listener was up | `alert_events.via = 'replay'` |
| eligible | passed the rules that apply on receipt: a row waiting to enter | `alert_events.status` |
| attempted | the paper book entered, at a print after the recovery | `fade_trades.est_entry` |
| simulated-filled | the nightly execution model priced that trade with no exclusion flag | ledger row with `via = 'replay'`, label MODELED |

The nightly health line gains a clause between the decision record and the database: `alerts: in order, none
recovered, no gap`, or the counts by stage, or a warning. When a registered review is reported and its sample
contains a recovered trade, the average is given with and without it; the registered criteria are read on the
registered definition, as written.

## 6. Where the build differs from the design of the same day

| Design (`ops_risks`, section 2) | Built | Why |
|---|---|---|
| unique index on `fade_trades(channel, message_id, symbol)` | a separate identifier table, key (channel, message id, symbol, alert kind) | alerts that leave no row need an identifier too (not a tested kind, duplicates); and a unique index over existing rows could fail at start-up on old data, which would stop the listener |
| cursor moved in the alert's own transaction | cursor moved in its own transaction that verifies every alert of the message is on record | a message can carry several alerts; one cursor move per message, checked, gives the same guarantee without one long transaction |
| at most 200 posts, none older than the current session day; beyond that "stop" | 200 a pass, continued in order; only posts older than 8 days are passed, with a gap row | "stop" would have left the cursor stuck. Yesterday's alerts cannot trade, but they are events for the 7-day repeat rule, so they are recorded |
| recovered and stale: row `filtered`, reason "recovered after a restart, too old to trade" | `skipped`, "alert received late", plus the replay label in its own columns | the existing reason already means exactly this and already counts for the repeat rule; a new reason would have needed that rule changed |
| (not in the design) | the push path waits for the catch-up at start; restart floors for entries; the stretched-window fix | found while building (sections 1 and 2) |

## 7. Not covered (tracked)

| Item | Status |
|---|---|
| A post Telegram never delivers to this account, or one deleted before the listener is back | cannot be recovered; the nightly decision-record check still names it if the channel's own record has it |
| An alert received late WITHOUT a restart (a connection gap while the process runs) | unchanged: the existing late rule, labelled `poll` with its delay. The print-after-receipt floor is not applied to it, because that would change the frozen rule for alerts handled live. Open: propose separately if wanted |
| A failing channel check while running does not close the push path | by choice: closing it would make every alert late during a Telegram "slow down". Order is only guaranteed at start-up |
| Crash between a row's commit and its decision line | the row exists, the line does not: the nightly check reports "alert rows have no line" (existing behaviour) |
| Crash between a state change and its text | the text is lost, never doubled |
| The trade-state database's write lock in session hours | OPEN, unchanged by this work. The measurement added in #198 (every wait or hold of 0.25 s or more, in the nightly line) stays in place; the Sunday numbers do not establish production safety. This change adds one small write per message (the cursor), which that measurement will see |
| The research recorder of the channel | unchanged (its own hourly catch-up) |

## 8. Deployment plan (standing authorization: after the owner merges)

1. **When.** Nothing open, outside 09:25 to 16:00 ET, 15:45 to 16:10, 20:25 to 20:56 and 21:00 to 23:05 ET.
   The first start only sets the cursor, so the deploy itself replays nothing.
2. **Gate.** `scratchpad/deploy_dayN.sh`: clean `main` at the merged commit, ruff, the test suite, "nothing open"
   read from the runner, the window check, `fly deploy`, then the `charts` and `lab` jobs.
3. **Verify on the runner (read-only):** tables `alert_events` and `alert_gaps` exist; `fade_trades` has `via` and
   `recovered_at`; flag `alert_cursor:<channel>` is set within a minute and flag `alert_catchup` says ok; the
   heartbeat is fresh; `fade_off` and `long_off` are unchanged; after the channel's next post, one `alert_events`
   row per alert and the cursor at that message.
4. **First real proof.** The next deploy after this one restarts the listener with a cursor in place: any post
   during that restart must appear with `via = 'replay'`. Checked by query after that deploy and reported.
5. **Nightly.** The `alerts:` clause in the 20:50 ET line. A warning there is reported the same night.
6. **Roll back.** Deploy the previous commit. The schema change is additive (two tables, two columns the old code
   ignores). The cursor flag stays; when this change is deployed again it resumes from it, bounded as in section 4.

Runbook for a warning in the nightly line: `SELECT * FROM alert_gaps ORDER BY id DESC LIMIT 20` (what was passed and
why), flag `alert_catchup` (where the catch-up stands), audit events `alert_gap`, `alert_failed`, `alert_replay`.
An alert given up on as `failed` is in `alert_events` with that status; it is never traded afterwards.
