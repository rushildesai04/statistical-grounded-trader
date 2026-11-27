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
| Should the paid feed be bought now? | **No.** Run the free Stage 0 (built today), settle the borrow question, and buy the month only if a book is still a candidate |
| Is the alert universe exhausted? | **For longs: yes.** For shorts: not yet; it is one test away from an answer (section 5) |
| The single next action | **A point-in-time borrow test of the short rule on the training history** (section 5): about 2,300 alerts instead of the 36 traded so far. It needs two owner decisions (a data account; approval to run one registered analysis on development data). No spending |

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
| B1 | **Borrow executability of the short rule** (section 5) | replaces 36 trades of evidence with about 2,300 on the question that decides the book | one registered run, both strata reported with intervals | a result and a keep / retire recommendation | owner: a data account and approval of one analysis on development data | data covers under 80% of alert stock-days: stop, report, no run |
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

Why B1 ranks above everything else in B and C: it is the only item whose result changes what the short book is.
B2 improves a measurement; B3 unblocks a purchase that is not yet justified; C2 cannot be hurried. If B1 says the
borrowable subset is negative on 1,000 or more trades, the short book stops being a real-money candidate at this
kind of broker, the paid feed loses its main customer, and the remaining effort moves to the two records that do
not need a borrow. If it says positive, the book has for the first time a sample that supports it, and the feed,
the locate question and the cohort all become worth their cost.

## 5. Recommended next action: a point-in-time borrow test of the short rule on the training history

**The hypothesis, stated so it can fail.** The tested short edge is concentrated in stocks with no shares to
borrow. If that is true, the rule's mean on alerts where the broker listed enough shares the evening before is at
or under zero, and the edge is not executable at a broker of this kind.

**Why it is causal and not a chart explanation.** A stock that can be shorted freely is shorted by others on the
way up; the squeeze-then-collapse pattern the rule harvests is strongest where short selling is constrained
(limits to arbitrage). The mechanism predicts the split before looking at it, and the three small observations in
section 0 all point the same way.

**What earlier tests actually addressed.** None used point-in-time availability. The held-out secondary used a
present-day easy-to-borrow flag (look-ahead, 64 trades). The live and modelled splits use the right data but hold
36 and 29 trades. The registered forward split beside F1 will answer the same question on about 300 alerts, in
January, as a direction. This test asks it on the roughly 2,345 training alerts (2024-01 to 2026-06) now. It does
not duplicate the forward split; it is the historical counterpart on development data.

**Plan.**

| Step | What | Who | Cost |
|---|---|---|---|
| 1 | Choose the availability source. Candidates: (a) QuantConnect's US Equities Short Availability: shortable quantity, fee and rebate per symbol per day from January 2018, 10,500 US equities, free inside its cloud (official pages; export terms, snapshot time and small-cap coverage are not stated there). (b) The stock-loan history tool of an Interactive Brokers account (about 3 years; reported, not verified). (c) A paid estimate vendor: not proposed | owner decides: (a) and (b) both need an account, and (a) means the alert list (symbols and dates) is processed on that platform | $0 for (a) as described; (b) unverified |
| 2 | Coverage check, no outcome read: for the training alerts, the share of stock-days the source has a row for on the session before the alert | me | none |
| 3 | Register, before any result: populations, definitions, the single run, the decision rule (draft below) | me; owner approves | none |
| 4 | Run once. Report every stratum with its count, mean and interval | me | about a day |

**Draft registration (NOT registered, NOT run).**

- *Population:* the training alerts of the registered rule (`prereg_alert_fade_2026-11-01`), same replay, same costs.
  The held-out file is not opened. The forward record, the cohort and H1 are not touched.
- *Borrow state, from the last list before the alert's session:* LISTED ENOUGH (shares at least the registered full
  paper order, $5,000 at the entry price), LISTED SHORT (fewer), ZERO LISTED, NOT ON THE LIST, UNKNOWN (no row).
  Five strata, never pooled; the fee is taken from the same row and charged pro rata for one day in place of the
  assumed 0.5%.
- *Primary:* mean net of LISTED ENOUGH, bootstrap by session, one-sided 95% lower bound.
- *Decision:* lower bound above zero: the book has a borrowable sample that supports it; proceed to the locate and
  feed questions. Mean at or under zero: recommend retiring the short rule as a real-money candidate at a broker
  with this kind of list (paper measurement and the registered reviews continue unchanged). Otherwise: no change.
- *One run.* No second definition of "enough", no second window, no re-cut by price or news after the result.

**Validation boundary.** Development data: the rule was built on these alerts, so a positive result is not a
confirmation and would still need the forward cohort. A negative result needs no such caution: an optimistic test
that fails is informative.

**Strongest counter-argument.** An evening list overstates what could be borrowed the next day in the middle of a
spike: shares vanish when a stock runs. So LISTED ENOUGH is an upper bound on what was executable. That biases the
test toward the rule, which is the right direction for a test whose useful outcome is a rejection. Second: if the
source's coverage of small caps is thin, UNKNOWN swallows the sample; step 2 exists to catch that before anything
is run.

**Applicability limits.** One broker's list; one size ($5,000); intraday fee treatment differs by broker; a locate
desk at a specialist broker is a different product with a different cost and is not tested here.

**Stopping conditions.** Coverage under 80% of alert stock-days: stop before running. The run is the end: no
variant follows it whatever it shows.

**What would change my mind about recommending it.** Evidence that a point-in-time source cannot be had without
spending; or the owner preferring to wait for the January forward split, accepting that it gives a direction on
about 300 alerts instead of an estimate on about 2,300.

## 6. If the short rule is rejected

Then this alert universe has produced no executable edge on either side, after a long list of registered tests,
and further cuts of the same alerts should stop. One bounded alternative is already running at no cost and needs
nothing new: the stock-loan list as its own event source (`prereg_borrow_scan_forward_2026-11-18`: shares drying
up, fees jumping; scored long and short over 1 and 5 sessions; decided once at 300 events with a result). Its
rationale is the same mechanism read from the other side: every event is, by construction, a change in whether a
stock can be shorted. Until it decides, nothing about it is read but its counts. No other new direction is proposed
here; proposing one without a mechanism and a point-in-time input would be adding cases.
