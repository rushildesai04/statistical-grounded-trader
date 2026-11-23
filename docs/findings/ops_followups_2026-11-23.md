# Operational follow-ups after #204 (2026-11-23, cohort day 1)

Owner, 2026-11-23: "#204 is already merged ... complete deployment verification ... Keep the remaining risks accurately
bounded." Paper only. No trading rule, no cohort definition and no rule-version label is changed by anything here.

## 1. Status

| | |
|---|---|
| #204 merged | Mon 2026-11-23 10:52 ET (main `9a4cc65`), inside session hours |
| Running at the time of writing | runner **v110 = main `d603298`** (#203), since Mon 01:34 ET. #204 is NOT running: the deploy script refuses 09:25 to 16:10 ET and was not bypassed |
| #204's deploy | waits for 16:11 ET, then the usual guards (clean `main` at `9a4cc65`, tests, nothing open, windows); it tries again every 5 minutes while an alert trade is open, until 20:20 ET. It runs on the owner's Mac: it needs that machine awake, not a chat session |
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
the owner's three steps above; nothing was created, signed up for or paid.

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
2. Warn early: the nightly line says so at 70% used (the 09:20 check already texts at 90%). Not built; a few lines.
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
