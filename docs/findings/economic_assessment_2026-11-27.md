# Economic assessment of the books, the work queue, and the next action (2026-11-27)

Written for the owner's instruction of 2026-11-27: judge the work against the economic objective, rank what is worth
doing, and recommend one next action with a plan, a cost, a validation boundary and a stopping condition. It builds
on `edge_assessment_2026-11-25.md` (the reconciliation and the sample-size arithmetic are there and are not repeated)
and uses only figures already shown in the nightly report of 2026-11-25 and in registered documents. Nothing sealed
was opened. Everything is paper.

## 0. The answer first

| Question | Answer |
|---|---|
| Is any book showing credible progress toward positive net expectancy? | **No.** Shorts: the only rule that ever passed a test (+1.12% a trade, lower bound +0.05%) reads +0.11% on the same replay forward (120 alerts) and -3.49% as traded live (36). Longs: negative in every record, tests included |
| What is the largest unresolved economic constraint? | **Whether the short edge exists in stocks that can actually be borrowed.** Every measurement closest to execution says it does not: IBKR-listed live shorts -4.59% (28) against about +0.4% for the other 8; names Alpaca lists easy to borrow -0.55% (64, held-out, look-ahead status); modelled shorts with no exclusion flag -8.87% (15) against +6.29% for flagged ones (14). Small, overlapping samples: an observation three times over, not a confirmation |
| Will the scheduled reviews settle it? | **Not soon, and mostly only in one direction.** At about 19 points of dispersion a trade, the reviews on the calendar can drop a rule that is at or under zero; none can confirm a +1.1% edge before roughly 800 to 1,900 trades (`edge_assessment_2026-11-25.md` section 7) |
| Is the live data feed the constraint? | **It is a real measurement and selection problem, not the binding economic one.** It changes which alerts become trades and when stops are seen (section 3). It cannot create an edge that the full-tape record does not show |
| Should the paid feed be bought now? | **No.** Run the free Stage 0 (built today) first; buy the month only if a book is still worth researching with it once the forward records have spoken |
| Is the alert universe exhausted? | **For longs: yes.** For shorts: not yet; the open question is borrow, history cannot answer it with any source available today (section 5), and the forward records will |
| The single next action | It was: establish whether a valid point-in-time borrow source exists. **Done the same day; the answer is no** (section 5, `borrow_source_feasibility_2026-11-27.md`). The historical study is withdrawn. The borrow question is answered forward, by records already registered. The most valuable work that can be done now is therefore operational: the deploy and the Stage 0 recorder (section 4, A1 and B2) |

## 1. The books: what is supported, what is failing

Figures: nightly report of 2026-11-25 (settled, official), except where a registered document is named.

| Book | Record | Against its test | Reading |
|---|---|---|---|
| Alert fade, as traded (paper, 25% size) | 36 trades, -3.49% a trade, book -5.33% | no-news: 20 live at -2.91% (interval of about 8 points either side) against +1.70% tested | **Failing as traded.** The sample cannot tell a bad rule from a bad month; the split by borrow is the part that matters |
| Alert fade, every alert, forward replay (F1) | 120 alerts, +0.11% | +1.12% tested; consistent with the test and with zero | **Unresolved.** Decided at the first month-end with 300 or more |
| Alert fade, executable baseline (cohort from 11-23) | 3 of 50 shorts with no exclusion flag, -0.7% at real quotes | none yet | **Collecting.** Early look at 25, review at 50 |
| Long book (paper, test size) | 28 trades, -2.30%; shadow book 191 trades, -2.43% | tested -2.1% to -0.3% | **Failing, as tested.** It is behaving like its test, and its test is negative |
| Megacap long calls (ledger) | 7 sessions, 55 calls, -0.27% a call | +0.23% tested, 54% winners | **Too early; small expected edge.** Decided 2027-04-30 |
| Megacap daily models | 18 sessions: model +7.1%, holding the same names -5.0% | historical Sharpe 0.6 to 0.7 against a bar of 1.0; unseen names 0.44 | **Measurement only.** A few weeks dominated by one down move; not evidence |
| Scanner | 5 trades, paused by the owner | tested -1.2% a trade | **Stopped.** Nothing is pending |
| 60/40, turn-of-month, yardsticks | as designed | | not trading questions |

Two facts about the short book that the totals hide:

- **Its outcome is two-humped.** A stopped trade costs about 22 points; a trade that is not stopped earns about 9.
  The whole tested edge is a stop-out share of 25.4% instead of the 29.0% at which the mean is zero. Four or five
  extra stop-outs in 120 trades erase it. That is how thin the margin is, and why hundreds of trades are needed.
- **The traded subset does worse than the full set**, and the gates do not explain it favourably: shorts not taken
  by the news, hours, repeat and beta gates since 11-06 averaged -0.4%, +1.3%, +2.0% and +8.6% (stock-days 53, 13,
  13, 5; in no total). Too few to act on. Worth knowing: the gates are not demonstrably adding value.

## 2. What the scheduled reviews will decide, and when

| Review | Decides | Count now | Can it confirm an edge? |
|---|---|---|---|
| F1, forward replay of every alert | keep / drop / extend to 600 | 120 of 300 (about 7 a session: 300 near early January, decided at that month's end) | drop: yes. Confirm +1.1%: unlikely before 800 or more |
| Flagged against unflagged split beside F1 | whether the forward result differs by exclusion flag | same sample | a direction, not a size |
| Executable baseline, shorts | the registered criteria of `exec_baseline_2026-11-21` | 3 of 50 | only an extreme result is readable at 50 |
| Executable baseline, longs | same | 2 of 30 | same |
| H1 (`short-ssr-limit-v1`) | sealed | sealed until its 30th event or 2027-02-26 | |
| Borrow-list events (dry-up, fee jump) | 8 tests, once | 77 and 25 events with a 5-session result, of 300 needed | weeks away |
| Megacap long calls | 2027-04-30 | 7 sessions | |
| Owner's bar for spending on a book | 300 live trades, interval above zero | 36 live shorts (about 2 a session: most of a year) | |

The uncomfortable arithmetic: at today's pace the short book reaches a count that could confirm its tested edge in
late 2027 at the earliest. Waiting is a decision to not know for a year. Section 5 is the way to know sooner.

## 3. Data and execution: what the feed does and does not explain

Supporting evidence that the live feed changes outcomes: the long book took setups that exist only on the free feed
(November: 12 of 31 base breaks, `long_bot_replay_2026-11-19.md`); the texted estimate of a short differs from its
official result by 1.25 points in the typical trade, and by +0.77 on average in the flattering direction; on one
thin stock-day replayed today, 133 of the 279 minutes with no free-feed candle traded on the tape
(`proposal_realtime_feed_shadow_2026-11-27.md` section 4).

Strongest counter-evidence that it is the binding constraint: the long rule replayed on the free feed's candles did
no worse than on real candles (-1.34% against -2.07%, 2,464 alerts, `long_feed_replay`, 2026-11-18); an exact,
zero-delay version of the trader's entry loses (69,720 crosses, 2026-11-18); and the short rule's forward replay,
which never touches the live feed, is itself only +0.11%.

Conclusion: the feed is worth measuring for free now and worth paying for only once a book is a live candidate.
What would change this: Stage 0 showing that a large share of the short book's entries or stops were decided on
prices the tape did not have at that moment.

## 4. The work queue, ranked

Each active task: intended improvement / acceptance criterion / deliverable / blocker / stop condition.

**A. Immediate correctness and safety**

| # | Task | Improvement | Accepted when | Deliverable | Blocker | Stop |
|---|---|---|---|---|---|---|
| A1 | Deploy the merged fixes (early-close candle filter, record-only longs, quote beside reference, closed-day report) | the runner runs the code the record describes | runner reports the merged commit; columns present; flags unchanged; no alert trade was open | deploy result | none: queued behind the session guard | a red gate or an open trade: not deployed, reported |
| A2 | Today's exits against the 13:00 close | know whether the old candle filter booked anything after the bell | every exit of the day placed by decision time, candle interval and settled time | a table, no record changed | none | none |

**B. Data and execution limits that prevent valid conclusions**

| # | Task | Improvement | Accepted when | Deliverable | Blocker | Stop |
|---|---|---|---|---|---|---|
| B1 | **Borrow-source feasibility** (section 5): DONE 2026-11-27, verdict "not established" | says whether the question "is the short edge in borrowable stocks" can be asked of history at all | every item answered from an authoritative source or from our own stored lists, or named as unanswerable | `borrow_source_feasibility_2026-11-27.md` | closed | stop condition met: the source cannot be shown point-in-time and a daily list misstates the alert-time state for about one alert in four. No study on it |
| B2 | Stage 0 feed recorder | says, per session, how much of "no candle" was no trade, another exchange, or missing data, and how late candles arrive | 5 sessions of nightly lines with zero dropped looks and zero write failures | `reports/feed_shadow/daily.jsonl`, one summary after 5 sessions | none: built, in review | it slows the book's pass or fails writes: it switches itself off; then removed |
| B3 | Paper-key entitlement at the data vendor | removes the gating unknown of any purchase | the vendor's written answer | the answer, filed with the proposal | owner: only the account holder can ask | answer is no: the Alpaca path is closed and section 3 of the proposal is redone |

**C. Strategy hypotheses worth investigating**

| # | Task | Note |
|---|---|---|
| C1 | B1 is the only one worth starting now | it decides whether C2 to C4 matter for the short book at all |
| C2 | The registered forward records (F1, cohort, H1, borrow-list events, megacap ledger) | running; no action improves them; dates in section 2 |
| C3 | Whether the live gates add value | not startable: 84 stock-days, in no total; revisit when the gate check reaches a count its own registration names |

**D. Low value: deferred or stopped, with the reason**

| Item | Why |
|---|---|
| Any further long entry, exit, timing or label work on these alerts | about 60 slices and more than a dozen registered variants, none positive in both samples; another variant has no decision value. The book stays on at test size by the owner's word; engineering on it stops unless a defect is found |
| Streaming data transport | only matters if the paid trial shows polling cannot meet the 5 s criterion |
| `long_book.py` split (about 1,400 lines) | maintenance; no decision depends on it |
| Scanner's fixed 16:00 | the scanner is paused |
| The short card's -22.4% against the test's -22.3% | a rounding difference on a text card |
| A test that fails only when run alone (import order) | passes in the gate; no risk to the record |
| Wording of model-book lines on a closed day | cosmetic |
| More documentation-only PRs | none planned; today's are consolidated into one |

Closed with evidence: the live candle reader's 1,000-row limit cannot truncate a session (04:00 to 20:00 is 960
minutes); the closed-day report was not a failed job (job log: sent, no failure marker) and its false "channel
quiet" warning is fixed.

B1 was ranked first because it was the only item aimed at the question the short book turns on, and it could be
settled quickly either way. It was settled the same day, against the shortcut. With it closed, nothing in C can be
started validly at no cost, so the queue's live items are A1, A2, B2 and B3: the first two are in hand, B2 waits
for its merge, B3 is the owner's question to the data vendor.

## 5. The borrow question: source checked first, historical study withdrawn

Owner 2026-11-27: "Prioritize validating the borrow-data source before approving the proposed outcome test." and
"Prepare one registered development-data study only after feasibility is established." The first version of this
section drafted the study before the source was validated and made two claims that are withdrawn:

| Withdrawn claim | Correction |
|---|---|
| The study "decides" whether the short rule is a real-money candidate | A study of this kind could at most inform whether to continue researching the short rule. It could not decide live readiness: listed shares are not a locate, a locate is not an approved order, and an approved order is not a fill |
| An evening list is an upper bound on what could be borrowed the next day, so a negative result is decisive | Measured on our own stored lists and false: of 320 alerts with shares listed both the evening before and at the alert, the count at the alert was HIGHER in 68 and lower in 183; of 420 listed the evening before, 100 were off the list at the alert (`borrow_source_feasibility_2026-11-27.md` section 4). A negative result would not have been decisive |

**The hypothesis that made the question worth asking** (still only a hypothesis): the tested short edge is
concentrated in stocks with no shares to borrow, by limits to arbitrage. Three small, overlapping observations
point that way (section 0). None is a point-in-time test on a large sample.

**Feasibility result (no cost, no account, nothing uploaded, no result read).**

| Question | Finding |
|---|---|
| Source identity | sound: the candidate history is a daily copy of the same public broker file this project stores |
| Point-in-time | not verifiable: the hour of the daily pull is not published |
| Terms | data may not leave the vendor's platform; a study there needs an account, accepted terms and the alert history placed there: three decisions not made |
| Resolution | a daily list is wrong about the alert-time state for about one alert in four on the "listed" side alone |
| Execution meaning | quantity, annual fee, locate charge and broker approval are four separate things; a list carries the first two only |

**Consequence.** No development-data study is prepared. Had one been run on a daily list it would have measured
"listed the day before", and the mixing measured above would have blurred any real difference. The question stays
where it can be answered properly: on the forward records, which stamp every alert with the list's own state and
time (the split beside the forward record, read once at the first month-end with 300 or more; the executable
baseline, early look at 25).

**What a future registration would have to state, should a valid source ever exist:** the existing entry and exit
rule unchanged, on training alerts only, held-out closed; the position size as it would have been at each alert,
fixed before the run and never searched; the eligible universe and the reason for each exclusion; the borrow state
from the last observation provably available before the alert; quantity, annual fee, locate charge and broker
approval kept apart, the last two recorded as not observed; missing data as its own stratum, described before any
return is read; cost assumptions labelled as assumptions; what a positive, a negative and an inconclusive result
would each justify, which is at most more or less research; one run.

**What would change this.** A source with verified timing and resolution near the alert that needs no decision the
owner has not made. None is known.

## 6. If research on the short rule is stopped

Then this alert universe has produced no supported edge on either side, after a long list of registered tests, and
further cuts of the same alerts should stop. One bounded alternative is already running at no cost and needs
nothing new: the stock-loan list as its own event source (`prereg_borrow_scan_forward_2026-11-18`: shares drying
up, fees jumping; scored long and short over 1 and 5 sessions; decided once at 300 events with a result). Its
rationale is the same mechanism read from the other side: every event is, by construction, a change in whether a
stock can be shorted. Until it decides, nothing about it is read but its counts. No other new direction is proposed
here; proposing one without a mechanism and a point-in-time input would be adding cases.
