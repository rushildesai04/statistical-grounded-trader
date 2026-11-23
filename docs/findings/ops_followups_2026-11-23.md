# Operational follow-ups after #204 (2026-11-23, cohort day 1)

Owner, 2026-11-23: "#204 is already merged ... complete deployment verification ... Keep the remaining risks accurately
bounded." Paper only. No trading rule, no cohort definition and no rule-version label is changed by anything here.

> **Update, 11:10 to 11:40 ET (owner: "#205 is already merged too").** Sections 1, 3 and 4 below are corrected in
> place where marked "(update)"; section 6 is new.

## 1. Status

| | |
|---|---|
| #204 merged | Mon 2026-11-23 10:52 ET (main `9a4cc65`), inside session hours |
| Running at the time of writing | runner **v110 = main `d603298`** (#203), since Mon 01:34 ET. #204 is NOT running: the deploy script refuses 09:25 to 16:10 ET and was not bypassed |
| #205 merged (update) | Mon 2026-11-23 11:07 ET (main `a8267a9`), also in session |
| The queued deploy (update) | **exact commit selected: `a8267a9`**, which holds both #204 and #205 (the first queue, for `9a4cc65`, was stopped at 11:09 ET and replaced). It waits for 16:11 ET, then the usual guards (clean `main` at `a8267a9`, the test suite, nothing open on the runner, the windows); it tries again every 5 minutes while an alert trade is open, until 20:20 ET |
| How durable that queue is (update) | It is a process on the owner's Mac, checked running at 11:10 ET, detached from the chat session (it survives the chat ending) and holding a no-idle-sleep request while it runs. It is NOT guaranteed: closing the lid on battery, logging out, a shutdown or restart of the Mac, a lost network, or an expired platform login stops or defeats it. If it does not run, nothing is harmed: v110 keeps running and the deploy is one command by hand: `scratchpad/deploy_day21.sh` |
| What v110 does not have yet | the long-book and alert-intake signs of life for the watchdog, the research catch-up off the event loop, the measurement of every stop-check interval, the alert-intake part of the 09:20 check. All four start with v111 |
| The 09:07 ET look promised from the chat session | fired at 10:52 ET, when the session next became active. It was never monitoring, as stated on 2026-11-23; the runner's own 09:20 ET check ran on time (`preflight_at` 09:20:00 ET) and sent nothing, which is its "all good" |

Read-only on the runner at 10:53 ET, v110, 83 minutes into the first cohort session:

| Check | Result |
|---|---|
| Listener | heartbeat 10 s old; both books on; not paused; catch-up state ok; no watchdog text |
| Alert intake | 34 alerts handled today, all by push; cursor at the newest message (27302, moved 10:52:23 ET); no gap row; no alert recovered (no restart) |
| Integrity | every alert row has its identifier, every identifier its row; no two rows share a message, stock and kind |
| Receipt delay today (post to book) | median 10 s, 90th percentile 42 s, longest 56 s; none over 60 s |
| Decision record | 46 lines today, every one with the full rule-version label (restart rules named) |
| Paper only | checked on the runner at 10:57 ET: `SGT_PILOT_SCHWAB` is not set (the real-money pilot stays off); the only broker-related secrets present are the market-data keys; the pilot job runs every 5 minutes and prints nothing. Both alert books are rows and texts, no order |

## 2. Session-hours measurements so far (production, partial: 09:30 to 10:53 ET, v110)

These are the first production numbers. They cover 83 minutes of one session on v110 and are not a session's
distribution; the nightly line at 20:50 ET has the full day, and v111's interval counts start the session after it
is deployed.

| Measure | Value |
|---|---|
| Writes that waited 0.25 s or more for the database | 0 |
| Writes that held the lock 0.25 s or more | 1: 0.30 s at 09:30:32 ET, the beta table's write at the open (`beta.py:betas`) |
| Stop checks more than 25 s (shorts) / 35 s (longs) late | 0 |
| Listener restarts | 0 |
| Every interval between stop checks | NOT measured on v110 (only late ones are). No idle-hour figure is offered in its place |

## 3. A machine that is down: the smallest outside probe (prepared, NOT provisioned)

**The gap.** Every check runs on the runner. A dead machine is silent.

| Option | How | Needs from the owner | Cost | Detects | Weakness |
|---|---|---|---|---|---|
| **A. Dead-man's switch at a monitoring service (recommended)** | the per-minute control job calls one address; the service notifies when calls stop for N minutes | an account at such a service (for example healthchecks.io), one check with period 1 minute and grace 3 minutes, a notification target (email or a phone app), and the check's address stored as a runner secret: `fly secrets set SGT_SECRET_DEADMAN_URL=... -a sgt-runner` in the owner's own terminal | the free plan is reported as 20 checks with email; SMS on the free plan is reported inconsistently. To be read on the vendor's pricing page before relying on it | machine down, schedule dead, no network; and, by a "/fail" call, a listener silent in alert hours even when the text service is down | one more outside party sees that calls happen (the call carries no data); its own outages |
| B. A scheduled job at GitHub that asks the runner | a workflow every 5 minutes requests a health address | a reachable health address on the runner (does not exist; a new public surface), repository secrets | free minutes on a private repository are reported as about 2,000 a month; every 5 minutes would use most of them | machine down | scheduled workflows are reported to start minutes to hours late and cannot run more often than every 5 minutes; a new inbound surface |
| C. A second machine | a tiny second host that watches the first | approval for paid infrastructure | paid | machine down | cost; a second thing to keep alive |

Prepared for option A, inactive: `sgt.ops.deadman` (one GET a minute from the control job, 5 s timeout, never
raises, nothing sent unless the secret holds a plain https address; `tests/ops/test_deadman.py`). Turning it on is
the owner's steps below; nothing was created, signed up for or paid.

**From the vendor's own pages (update; read 2026-11-23).**

| Fact | Vendor text | Page |
|---|---|---|
| Free plan | "Hobbyist", "$0 / month", "Monitor 20 jobs", "100 log entries per job" | [pricing](https://healthchecks.io/pricing/) |
| SMS, WhatsApp, phone calls on the free plan | no credits are listed for it; the first plan that lists them is Business, "$20 / month": "50 SMS & WhatsApp credits", "20 phone call credits". So: plan on a channel that needs no credits (email or a phone app). Which channels the free plan includes is not stated on the pages read: to be seen when the account is made | [pricing](https://healthchecks.io/pricing/) |
| The address to call | `https://hc-ping.com/<uuid>`; a failure is the same address with `/fail`; "HEAD|GET|POST" | [pinging API](https://healthchecks.io/docs/http_api/) |
| How often | "If you ping a check more than 5 times per minute, some of the requests may get rate limited". The probe calls once a minute | [pinging API](https://healthchecks.io/docs/http_api/) |
| Period and grace | "Period is the expected time between pings"; "Grace Time is the additional time to wait before sending an alert when a check is late" | [configuring checks](https://healthchecks.io/docs/configuring_checks/) |

**Setup (the owner's, about five minutes; the address is a secret and is never pasted into a chat or a document).**
1. Create an account at the service and one check. Schedule: period 1 minute, grace 3 minutes.
2. Add one notification channel that reaches a phone without credits, and send its test notification.
3. In the owner's own terminal, store the check's address on the runner (it restarts the runner: outside 09:25 to
   16:10 ET and with nothing open):
   `fly secrets set SGT_SECRET_DEADMAN_URL='<the address>' -a sgt-runner`
4. Tell the assistant only that it is set. It then verifies, without reading the secret, that the check shows
   calls arriving and that stopping the control job for five minutes produces the notification.

To switch it off: `fly secrets unset SGT_SECRET_DEADMAN_URL -a sgt-runner`.

**It cannot hold up trading (update; tests in `tests/ops/test_deadman.py`).**
- No trading task contains it: the only code that calls it is the per-minute control job, a process separate from
  the listener (`test_nothing_in_the_listener_or_the_books_ever_calls_the_outside_probe`).
- In that job it is the last step, after owner commands are handled and the text connection is closed.
- A slow answer, an outage or a name lookup that never returns costs that job at most 6 s: the call runs on a thread
  that is abandoned (`test_a_call_that_hangs_is_cut_off...`).
- A failed call changes nothing on the runner; it is the outside service that then reports silence.

**What an "ok" call means (update).** Not "the scheduler ran". The control job calls "ok" only when the listener
shows all its signs of life at that hour: the short book's stop check and the long book's (every 10 to 15 s around
the clock) and the reading of the channel (every 15 s in alert hours, every 5 minutes otherwise). With any of them
silent it calls "/fail". If the job cannot read the database, or the machine or its schedule is dead, no call is
made and the service reports the silence. Texts about a silent listener remain an alert-hours matter; the probe
judges at any hour.

Sources for the reported plan limits (third-party pages; the vendors' own pages were not read):
[healthchecks.io pricing](https://healthchecks.io/pricing),
[plan summary](https://toolradar.com/tools/healthchecks-io/pricing),
[GitHub Actions schedules](https://cronuru.com/guides/github-actions-scheduled-workflows),
[community report of delays](https://github.com/orgs/community/discussions/201738).

## 4. Cache retention: a proposal that deletes nothing now

Measured on the runner (read-only, 2026-11-23):

| Path | Size | What it is | Disposable? |
|---|---|---|---|
| `data/alpaca/bars_1m/date=.../symbol=...` | 1,796 MB, 60,976 files, from 2020-07-22 | one-minute bars the daily models train and test on (the registered forward test of the daily book reads them) | **No.** Registered-research data. A re-download can differ (vendor corrections), so a deleted partition cannot be assumed recoverable as it was |
| `data/borrow` | 145 MB | borrow-list snapshots, four an hour in session | **No.** The evidence behind every "no shares to borrow" decision |
| `data/fast` | 8 MB | the alert books' own bars: what official results were computed from | **No** |
| `reports/**` (ledgers, forward records, ops) and both databases | 55 MB | the record | **No** |
| `reports/charts` | 13 MB | pages rebuilt every 10 minutes | yes, regenerated |

Every file under `data/alpaca` was modified in the last 30 days (the volume was filled this month), so "old by
modification time" selects nothing; the partition date is the only handle, and the old partitions are exactly the
training history.

**Proposal.**
1. Delete nothing now. Used: 2.10 of 5.18 GB; at most 91 MB was written in the last 7 days, so about 30 weeks of room.
2. Warn early (update: built). The warnings and where each one goes:

| Warning | When | Reaches the owner by | Status |
|---|---|---|---|
| "disk N% full ... add room before 90%" | every night from 70% used | the nightly summary text, 20:50 ET | built in the change that carries this update (`decision_health.disk`, tested) |
| "the server disk is N% full: jobs may start failing" | 90% used | a text at 09:20 ET on every session day (`scripts/preflight.py`) | live since 2026-11-08 |
| "The listener has not run for N min" | the disk is full and the heartbeat can no longer be written | a text within 3 minutes in alert hours (the watchdog), repeated every 30 minutes | live (v110); from this change it is spaced by a marker file, because on a full disk the database cannot record that the text went out and it would otherwise repeat every minute (found here, tested) |

**If storage becomes critically low (tested where marked).**
- No entry or exit can be booked; no text goes out for an exit that is not on record; no row is half-written
  (tested: `test_a_full_disk_books_nothing_texts_nothing_corrupts_nothing...`).
- The heartbeat stops, so the watchdog texts (tested), and the outside probe, once configured, reports "fail".
- Decision-record lines cannot be written or spooled: counted as lost and said in the nightly line (tested in #197).
- The nightly settlement cannot store the full-tape bars it scores from: trades stay unsettled until there is room.
- When room returns, a waiting exit is booked once, at the price of that moment (tested).
- Nothing is deleted automatically, ever. Making room is the owner's decision: growing the volume (paid) or
  removing regenerated output only (point 4).
3. At 70%, GROW the volume instead of pruning (a paid change: needs the owner's approval; the platform's volume
   price is to be read then).
4. If something must ever be removed: only regenerated output (`reports/charts`, caches of derived features), never a
   path marked "No" above; and only after a manifest with checksums, a copy off the machine, a dry run that lists
   every file, and the owner's confirmation. Deleting is the owner's decision each time.

## 5. Explicitly unresolved (neither needs a silent change)

| Item | State |
|---|---|
| A text lost when the process dies between a state change and its text | unresolved. Never doubled; never sent later; nothing records the loss. The trade is on the site and in the nightly summary. Trigger: a restart inside alert hours (warned about nightly). The fix, if wanted, is engineering (the text written in the state change's own transaction), not a strategy change; not built |
| An alert received late without a restart enters at the print of that moment | unresolved, and a trading-rule question. The registered rule and F5 say "first bar / first quote at or after alert + 10 s". Today: 34 alerts, receipt delay median 10 s, none over 60 s. Owner's decision, on the specification and pre-cohort evidence, not on the cohort's interim returns. Nothing prepared, nothing changed |

## 6. After the deploy (to be filled by the post-deploy check; nothing below is claimed until then)

The queued deploy's verification list, in this order: runner version and commit; the three signs of life present
and fresh (`beat.short_book`, `beat.long_book`, `beat.alerts`, and the heartbeat flag); the research catch-up's log
line from the new path; the first interval summaries in `loop_gaps.jsonl` (ten minutes after the start); both books
on, the real-money switch unset, the cursor where the last message is; no rule or cohort definition changed (the
diff `d603298..a8267a9` touches no book rule: checked before queuing). Session-hours numbers are reported from the
20:50 ET line; idle-hour numbers are not offered in their place.

## 7. Review of #206's two details and the probe's isolation (2026-11-23, about 12:00 ET)

| Question | Finding | Change |
|---|---|---|
| Does off-hours probe health follow the listener's own schedule? | Not at the change of pace. Outside alert hours the channel is read every 5 minutes, and the listener's last slow wait can end a minute AFTER alert hours begin (09:25 ET); judged by the fast pace (limit 3 minutes) a healthy listener was "alerts silent" for up to about two minutes every morning: a false text and a false "fail". **Found before it was ever deployed.** Books that are switched off or paused still check in (tested), so they never read as silent | for 5 minutes + 60 s after the pace changes the slower pace still applies (`test_the_first_minutes_of_alert_hours_are_judged_by_the_pace_the_listener_was_still_on`, `test_books_that_are_switched_off_or_paused_are_not_a_silent_listener`) |
| Is a full-disk warning still spaced when the marker file cannot be written either? | No: #206 assumed changing a file's time always works. On a full, read-only or failing volume it may not, and then nothing remembered the warning | when neither the marker nor the database can be written, the silence's own age is the clock: a warning in the minute it reaches 3 minutes and every 30 minutes after, no state read or written (`test_with_no_way_to_write_anything_down_the_warning_is_spaced_by_the_age_of_the_silence`: three texts in 70 minutes, not 70). Residual: two runs landing in the same minute of silence could send two |
| Is the probe isolated in behaviour, not only in the source? | Yes. A real second process makes the probe's call to (a) a local server that accepts and never answers, (b) a port nothing listens on, while an open short's stop is crossed: the stop is booked in under a second with the probe still waiting; the probe gives up inside its bound and reports "failed" | test only (`test_an_outside_probe_that_stalls_or_cannot_be_reached_does_not_delay_a_stop`) |

The one deploy queue reads its target commit from a file at 16:11 ET, so a newer merged commit replaces the target
without a second job; a second start of the queue is refused.
