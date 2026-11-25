# Is there a defensible edge? FCUV (2026-11-25) and the gap between the tests and the forward record

**Date:** 2026-11-25 (written about 13:30 ET, during the session; tonight's settle and ledger come after). Read-only investigation:
no rule, size, test, registered calculation or production behaviour is changed by this document. Runner v114 =
`c12aec4`. Owner, 2026-11-25: "prioritize identifying why SGT's forward performance is materially worse than its
historical tests, rather than continuing to patch individual losing trades ... The objective is not to explain away
losing trades. It's to establish whether we actually have a defensible trading edge and what evidence would be
required to demonstrate one."

Three records are kept apart throughout: the LIVE paper path (free feed), the SETTLED rule replay (official one-minute
bars), and the EXECUTION MODEL (real quotes, frozen v1, 0.5% locate ASSUMED). None is a broker fill. Sources: the
decision record, trade rows, the official tape (trades, quotes, bars, read once 15 minutes old), the free feed the
books read, the news feed, `reports/alert_fade/forward.csv`, `reports/exec_model/ledger.jsonl`, and the registered
results cited by file name. No held-out or training file was re-split for this document; where a question would need
that, it says so and proposes a registration instead.

## 0. The answer first

| Question | Answer |
|---|---|
| Does the short book show positive executable expectancy? | **No. Not demonstrated, and the evidence closest to execution is unfavourable.** The tested +1.12% a trade had a lower bound of +0.05%, assumed a 0.5% locate and never checked that a share could be borrowed. On the same replay the forward record is +0.11% (120 alerts, 18 sessions). The live book is -2.44% (34). On real quotes, pre-cohort shorts with no exclusion flag are -8.9% (15) |
| Does the long book? | **No, in every record that exists**, tests included: tested -0.79% / -1.12%, bot replay -0.89% to -1.85%, live -2.25% (26, 27% winners), real quotes -3.63% (18). This is unfavourable evidence, not missing evidence |
| Why is the forward record worse than the test? | Not the live code path: the forward record is the SAME bar replay as the backtest and never touches the listener. On it, stop-outs are 29.2% of trades against 16% tested (a session bootstrap puts 16% or less in 1 of 10,000 resamples); trades that are not stopped earn MORE than tested (+9.2% against +5.6%). Alerted stocks in November move further in both directions. With a 20% stop and winners of 6 to 9%, break-even is a stop-out rate of 20 to 30% |
| Why is the live book worse than the forward record? | Selection, on a small sample. The live book takes about a quarter of the alerts. Trades where IBKR listed shares average -3.3% (26); the others +0.4% (8). The test's own secondary said the same in 2026-11-01: names Alpaca lists easy to borrow, -0.55% (64). An observation from overlapping samples, not a confirmation (section 5, U1) |
| Did implementation defects cause it? | No defect found explains it. Six are listed in section 4; all are fixed, deploying today, or approved and unbuilt. None moves the forward record, which shows the shortfall without any of them |
| Was FCUV a defect? | The short: no. An eligible entry inside its registered minute, a correct no-news read, a stop detected 7.2 s after the tape. The long: a setup that existed only on the free feed, booked 66 s after its fill candle began, when the entry and the exit prices had both already printed: a known limitation of how trigger setups are recorded, about -$3 at the test size |
| What would demonstrate an edge? | Section 7. At the dispersion the forward record shows (about 19 points a trade, session-clustered), a true +1.1% needs roughly 800 trades to clear zero half the time and about 1,900 to do so four times in five. The registered decision at 300 forward alerts can drop the fade; it is unlikely to confirm it |

## 1. FCUV short (fade id 526)

| Step | Time (ET) | Fact |
|---|---|---|
| Before | | prior close 3.69, open 3.63, almost no trading until noon; a run from 12:11. No company news: 0 items on the feed, then and hours later. Beta 1.09. Not a repeat (last traded by us 2026-11-13). Price test not in force (low 3.44 against a prior close of 3.69) |
| Borrow, as listed | 12:03:07 | IBKR: 30,000 shares at 597.6% a year (snapshot 25 minutes before the alert). Alpaca: not shortable. Both are listings at one broker at one time; neither is a locate |
| Alert | 12:28:00 | "new high"; received 3.8 s later |
| Registered entry minute | 12:29 | the first minute at or after alert + 10 s; official open 5.575 |
| Live entry | 12:29:20.19 | 5.65, on a free-feed round lot of 12:29:20.062: inside the registered minute, so eligible under `live-entry-v2`. Official quote at the decision: 5.60 x 5.65, 100 shares each side, against an order of about 221 shares ($1,249 at a quarter of full size) |
| Halt | 12:29:43 to 12:34:43 | a volatility halt 23 s after the entry. Nothing could have been covered in those five minutes |
| Best point | 12:37:21 | low 5.1001: 9.7% in favour |
| Stop level 6.78 | 12:47:18.166 | first official print at or above it (6.79). Free feed's first round lot 12:47:23.111 (6.82). Detected 12:47:25.33: 7.2 s after the tape, 2.2 s after the feed, in continuous trading |
| Live estimate | | cover 6.82: -20.7% before costs, about -21.7% after (0.95% estimated costs); about -$271 |
| After | 12:48 | day high 7.00, then lower |

**The three records.** Live: above. Settled: tonight; the registered entry is the 12:29 open, 5.575, whose stop is
6.69, also reached in the 12:47 minute, so the settle is expected near -20% before costs. Execution model, 1 s after
the decision: sold at the 5.60 bid, stop 6.72 touched 12:47:16.25, bought back 6.72: -20.0% before costs, -20.5%
with the ASSUMED 0.5% locate; 5 s: -21.1%; its stress case -24.6%. Flag: thin (the quote was smaller than the order).

**What the model's cost does not contain.** A listing at 597.6% a year. One day at that rate is 1.6% of the position;
whether an intraday locate was obtainable, and at what fee, is not established by a listing. The test's break-even
locate was about 1.6% (`prereg_alert_fade_2026-11-01`).

**Classification.**

| Cause | Finding |
|---|---|
| Entry eligibility, news read, beta, repeat, price test | followed the registered specification; verified from the stored evidence, not assumed |
| Stop detection | 7.2 s after the tape in continuous trading. Not a detection failure |
| Implementation defect | none found |
| Strategy risk | the loss. A no-news run that the rule fades by design continued: +53% over the prior close at the entry, +90% at the high. This is the outcome the 20% stop exists for, and the outcome whose frequency is the subject of section 3 |
| Limitations that made it worse than the record says | (a) a halt 23 s after entry: for five minutes the stop was not an exit; (b) displayed size 100 shares against 221; (c) a borrow listing at 598% a year against a flat 0.5% assumption |

No rule is proposed from this trade.

## 2. FCUV long (long id 89)

| Step | Time (ET) | Fact |
|---|---|---|
| Allowed | 12:47:25 | our short had closed, so the no-long-while-short rule did not apply |
| Setup, FREE FEED | 12:49 to 12:50 | bull flag (L5v2): pole 5.15 to 6.86, flag low 6.18, high 6.57. Ready for the 12:51 candle: trigger 6.57, stop 6.18, target 7.44 |
| Setup, OFFICIAL TAPE | | **no bull flag detected.** The official candles gave only L4a/L4b at 12:35 and L6 at 12:41. Tonight's stamp will class it; on this reading it is feed-only |
| Fill candle 12:51 | | free feed: open 6.60, close 6.53: RED, which under the adopted red-candle exit means out at the next open. Official: open 6.32, high 6.73, low 6.251, close 6.53: GREEN. First official print at or above the trigger: 12:51:10.751 |
| Booked | 12:52:06.89 | entry 6.60 (the feed's 12:51 open). This is 66 s after the fill candle began and after it had closed red. The exit price, the 12:52 open at 6.53, had already printed. Quote at the decision: 6.50 x 6.59 |
| Exit decided | 12:53:07.6 | 6.53: -1.06% before costs, about -1.5% after; about -$3 on $210 (33 shares) |
| Execution model | | buy 6.59 at the decision, sell 6.53 at 12:53:01: -0.91%, at 1 s and at 5 s |

**Classification.** (1) A setup that existed only on the free feed: the known, registered limitation
(`prereg_long_setup_tape_2026-11-22`: feed and tape agree on 4 of 22; BNAI this morning was the same class). (2) The
trade was written down after it was over. A trigger setup fills inside a candle, and the book reads closed candles;
so the booking, the entry text and the exit all followed a candle whose outcome was already known. The entry
reference was not flattering here (6.60 booked against a 6.59 offer) but it was 66 s old, and the text told the owner
"in" when the rule had already said "out". This is the stale long-entry limitation the owner ruled on today
(decision 2: keep the registered reference, add a separately labelled estimate from current quotes). Not yet built.
(3) No rule defect, no new filter. The loss is the size of the spread.

## 3. The reconciliation, on comparable populations

### 3.1 Shorts: what was tested and what has happened since

| | Population | Method | Trades | Mean net | Winners | Stop-outs |
|---|---|---|---|---|---|---|
| T1 | held-out 2026-07-01 to 10-29, every in-cell alert (the registered primary) | bar replay, modelled stops, cost model + 0.5% locate | 764 | **+1.12%** (lower bound +0.05%) | 57% | 16% |
| T2 | T1 with real stop fills from the tape (`prereg_fade_stop_slippage_2026-11-09`) | | 764 | +0.91% | | |
| T3 | T1 with locate at 1% / 2% | | 764 | +0.62% / -0.38% | | |
| T4 | T1, names Alpaca lists easy to borrow (status of 2026-11-01, look-ahead) | | 64 | -0.55% | 44% | |
| T5 | held-out, no-news alerts, real stops (the basis of the live no-news rule, F6) | | 487 | +1.77% (lower bound +0.47%) | | |
| F | forward record 2026-10-30 to 11-24, every in-cell alert (amendment F1) | **the same replay as T1** | 120 (18 sessions) | **+0.11%** (median -1.3%) | 48% | **29.2%** (35) |
| F' | F without news-type alerts | | 111 | +0.46% | | 27.9% |
| L | live book, settled, 2026-11-03 to 11-24 (no-news, beta under 3, repeat skip, borrow and ops gates) | official bars from the registered entry | 34 | **-2.44%** (median -4.8%) | 41% | 29.4% (10) |
| M | pre-cohort shorts with no exclusion flag | execution model, real quotes | 15 | **-8.87%** (their settled figure: -9.18%) | | 6 |
| M' | pre-cohort shorts carrying an exclusion flag (not modelled) | settled figure only | 14 | +6.29% | | |
| C | the registered cohort from 2026-11-23, modelled | execution model | 3 (today's two not yet in) | -0.7% | | |

T1 and F are the comparable pair: same events definition, same replay, same costs, no listener, no free feed, no
live code. L, M and C are different populations (a filtered subset; a real-quote model) and are not pooled with them.

### 3.2 Where the gap between T1 and F is

Every trade is either stopped or not. Mean = (share not stopped x their average) + (share stopped x their average).

| | Stopped: share, average | Not stopped: share, average | Mean |
|---|---|---|---|
| Tested (T1) | 16%, -22.4% | 84%, +5.6% (implied) | +1.12% |
| Forward (F) | 29.2%, -21.95% | 70.8%, +9.2% | +0.11% |
| Live book (L) | 29.4%, about -21.5% | 70.6%, about +5.5% | -2.44% |

- The stop-out loss is the same size in all three. Stops are not slipping more than tested.
- In the forward record the trades that survive earn more than tested and stop-outs are almost twice as frequent:
  the distribution is wider on both sides. Sum of the 35 stopped trades -768 points; of the 85 others +782.
- Break-even stop-out rate: 20% at the tested winner size, 29.5% at the forward one. The forward record sits on it.
- Session bootstrap of F (10,000 resamples of the 18 sessions): mean, 95% interval -3.3% to +3.6%; the tested +1.12%
  or better in 28% of resamples. Stop-out rate, 95% interval 21.7% to 37.0%; the tested 16% or lower in 0.01%.
  **So the forward MEAN is not distinguishable from the tested mean; the forward STOP-OUT RATE is.** 17 of 18
  sessions had at least one stop-out.
- By week (alerts, mean, stop-outs): 10-30: 5, +1.9%, 2 · 11-02: 33, -3.0%, 10 · 11-09: 35, -1.2%, 11 · 11-16: 32,
  +7.6%, 6 · 11-23 (two sessions): 15, -6.4%, 6. One good week carries the month; that week is not evidence of
  recovery and the others are not evidence of a break.
- The live book's non-stopped trades match the test (+5.5% against +5.6%); its whole shortfall is the stop-out rate.

### 3.3 The five questions

**Selection bias, regime dependence, overfitting.**
- The registered primary passed narrowly: lower bound +0.05%. A result that close to its threshold is the kind
  that shrinks on new data whether or not anything changed. That is expected, not a discovery.
- 39 fade registrations exist; 31 refer to the held-out period. The primary was run once and stands. But the LIVE
  rule is not the primary: no-news (F6), beta under 3 (F7) and the repeat skip (F8) were each adopted after the
  held-out period had been opened, on registered analyses of data already seen (F7's own text: "Seen data"). The
  live book therefore trades a subset chosen after looking, and its tested figures (T5's +1.77%) carry a selection
  the primary does not.
- The backtest dropped events that were halted at the entry: 62 held-out events, mean -7.35%, 63% stopped
  (`prereg_fade_halt_audit_2026-11-09`). Found and corrected on 2026-11-09 by making the live entry the tested one.
- Regime: `prereg_fade_regime_gate_2026-11-13` found session results have almost no memory in 354 training sessions
  (lag-1 correlation +0.06); a poor week did not predict the next. Its own reading on 11-13: "November is the
  first time the fade actually broke." **Not determined:** whether an 18-session stretch with a stop-out rate near
  29% occurred anywhere in the history. Answering needs a time split of files that are not to be re-split without a
  registration (proposal P1).
- The forward record is one month from one channel.

**Do the historical samples represent borrow, spreads, liquidity and costs?**
- Borrow: **no.** The test charges a flat 0.5% and assumes a share is always there. Its own feasibility check: 97 of
  150 random training symbols not shortable at Alpaca. T4 is the only historical figure on borrowable names and it
  is negative. Today's two shorts were listed at 8.2% and 598% a year.
- Spreads and costs: **adequately, for shorts.** On the 15 pre-cohort shorts with no flag, real quotes give -8.87%
  against a settled -9.18%: the model is not worse than the replay. Forward cost averages 0.94% a round trip.
  The shortfall is not a spread the test forgot.
- For longs real quotes are worse than the replay by 1.4 points (-3.63% against -2.25%, 18 trades): the candle
  price the long book records is not the price on offer.
- Liquidity: 23 of 29 pre-cohort ledger shorts carry the thin flag (quote smaller than the order) at a quarter of
  full size. Whether the rest of the order fills, and where, is not established by any record here.
- The short-sale price test is not in the backtest. The registered check on 1,335 training trades did NOT find the
  result concentrated there (`prereg_fade_price_test_2026-11-21`); the forward observation that flagged trades
  carry the gains (M') stands beside that null, not above it.

**Do concentrated losses overwhelm the gains?** Yes, by construction, and it is the whole of the forward result.
A stop-out costs 22 points and a surviving trade earns 6 to 9, so one stop-out cancels three to four winners. The
strategy is short the tail. In the test the tail came 16 times in 100. Since 2026-10-30 it has come 29 times in 100.

**Does the free feed change eligibility or exit timing?**
- Shorts, settled record: no. It is scored on the official tape from the registered entry.
- Shorts, which trades exist: yes. 12 alerts were skipped because the free feed showed no print in five minutes; 2
  were entered on a later print (now record-only); the live and settled exit kinds differ in 3 of 34.
- Shorts, live stop detection: 4.8 s (ALGS) and 7.2 s (FCUV) today; GLXG's live stop never printed on the feed.
- Longs: materially. Feed and tape agree on 4 of 22 setups; 12 of 31 November bases were not on the tape (-2.8%);
  the feed's first print at or under a long's stop came a median 34 s after the tape and up to 21 minutes. Both
  long losses of 2026-11-25 were feed-only setups.

**Positive expectancy after realistic costs?** Section 0. For shorts: the single positive figure is the test's, and
every step toward execution lowers it (real stops, a realistic locate, borrowable names, the forward month, the live
book, real quotes). For longs: no positive figure exists.

## 4. Confirmed defects (implementation), ranked by effect on the record

| # | Defect | Status | Explains the gap? |
|---|---|---|---|
| D1 | The backtest dropped halted events; the live book had entered them (2026-11-09) | fixed 11-09; corrected pooled figure +0.86% | no: pooled it lowers the tested figure by 0.15 points (+1.01% to +0.86%); on the held-out period alone it raises it (+1.12% to +1.18%) |
| D2 | A later free-feed print stood in for a passed entry minute (BUUU, GLXG); delivery-late alerts likewise | fixed in #218 to #220; deploy queued for today 16:11 ET | no: 2 of 34 trades |
| D3 | Trigger-setup longs are booked after their fill candle closes; entry and exit references can both predate the booking (FCUV long) | approved today as a quote-based estimate beside the registered reference; not built. #221 (draft, review incomplete) records the reference | no: it misstates a long's price by about the spread |
| D4 | Long stops are seen only when a free-feed candle closes | between-candle check approved today for measurement; not built | no: it delays a paper exit; the settled record is on the tape |
| D5 | Binary rounding at exact stop and target touches | `price-decimal-v1` runs beside the registered arithmetic from today | no: 1 of 60 settled trades |
| D6 | Live estimates revised by 5 points or more at the settle (GLXG) | reported nightly from tonight | no: the settled figure is the record |

None of these touches the forward record F, which is where the shortfall against the test is measured.

## 5. Known limitations and unproven hypotheses, ranked

**Limitations (established facts about what the records cannot show).**
1. Borrow availability and cost at the alert are not in any historical figure. The largest unknown.
2. A stop is a trigger. Halts and gaps pass through it (held-out slippage: mean 1.44%, 90th percentile 3.17%).
3. Displayed size is usually smaller than the order, even at a quarter of full size.
4. The long book's setups and stops are defined on a feed that carries about 1% of the volume.
5. One channel, one forward month, 18 sessions.
6. The live rule is a subset chosen on seen data (F6, F7, F8).

**Hypotheses (not established; each needs unseen data).**

| | Hypothesis | For | Against, or missing | What would settle it |
|---|---|---|---|---|
| U1 | The tested gain sits in stocks that cannot be shorted as assumed (no shares, the price test, a prohibitive fee) | T4 (-0.55%, 64, registered secondary); live: IBKR-listed -3.3% (26) against +0.4% (8); ledger: no flag -8.9% (15) against flagged +6.3% (14) | the last two overlap and are one observation; both were examined after the fact; the price-test check on training was null; T4 uses a later borrow status | the sealed H1 result; the cohort review; a registered forward split on the borrow listing stamped BEFORE each entry (P3) |
| U2 | November is a different regime: alerted stocks move further both ways | stop-outs 29% against 16% and survivors +9.2% against +5.6%, on the same replay | 18 sessions; history not examined for comparable stretches | P1 |
| U3 | The channel or its audience changed (which alerts fire, how fast the crowd acts) | none measured | no comparison of alert mix or pre-alert moves has been made | P2, which reads no outcome |
| U4 | The original pass was partly luck (lower bound +0.05%) | arithmetic | cannot be separated from U2 with one forward month | only more forward data: F1 at 300 and at 600 |

## 6. Proposed improvements

Nothing below is built, registered or run. Each needs the owner's word. None adds a filter from FCUV, ALGS or BNAI.

| | Proposal | Expected benefit | Risk | Validation |
|---|---|---|---|---|
| P1 | **Registered diagnostic: is this month inside history?** On the training file only (354 sessions), the distribution of stop-out rate and mean net over every 18-session window, fixed before the run; where 29.2% and +0.11% fall in it. Adopts nothing | tells apart "a stretch the test already contains" from "something the test never saw"; decides whether the 25% size is a response to noise or to a break | a time split of a file used many times: it must be one registered run, with the reading rule written first; the held-out file stays closed unless the owner opens it | one script, existing files, no new data. Registration reviewed before the run |
| P2 | **Composition check with no outcomes:** alert-type mix, entry price, move before the alert, time of day, alerts a session: forward against history | detects a changed population (U3) without spending any outcome data | finding a difference invites a filter; the registration must say it adopts none | registered list of covariates; outcomes not loaded by the script |
| P3 | **Forward borrow split:** the IBKR listing (shares, fee) is already stamped before each entry. Register now: groups, a fixed count per group, one decision date. Every alert, not only taken trades | the first evidence on U1 that is not retrospective | slow (a quarter or more); listings are not locates | prospective only; rows before the registration date are development data and excluded |
| P4 | **Real locate quotes** for a sample of alerted names (amendment F1 already requires this before any capital) | replaces the 0.5% assumption, the input the result is most sensitive to (break-even 1.6%) | needs a locate-broker account: the owner's action, and no spending is authorised | a dated sample of quotes set beside the listing stamped at the same minute |
| P5 | Build the two measurements approved today (between-candle long-stop check; quote-based entry estimate with quote age, displayed size and slippage) | the long record stops describing prices and exits that were not available | measurement only; neither is stop protection or a fill | reviewed PRs; registered figures unchanged; shown beside, never instead |
| P6 | **Stop patching single losses.** A loss review reports facts and classification; a rule change needs a registered test on unseen data. Effort goes to P1 to P4 and the registered decision dates | ends the cycle in which each loss produces a candidate rule fitted to it | none to the record | the dates in section 7 |

Not proposed, and why: a tighter stop, a confirmation delay, a momentum filter, earlier profit-taking. Each was
tested and rejected in the playbook's section 4, or would be fitted to three trades.

## 7. What it would take to demonstrate an edge

**The registered decisions, unchanged.**

| Record | Decides at | Now | Rule |
|---|---|---|---|
| Forward record (F1) | first month-end with 300 or more | 120 in 18 sessions (about 6.7 a session: 300 near early January, decided at that month's end) | KEEP if mean above 0 and the lower bound above 0; DROP if mean at or under 0; else to 600 on the mean alone |
| Executable baseline, shorts | 50 modelled trades (early look 25) | 3, plus today's 2 pending | as registered in `exec_baseline_2026-11-21` |
| Executable baseline, longs | 30 modelled (early look 15) | 2 | as registered |
| H1 | 30th event or 2027-02-26 | sealed | as registered |
| Owner's bar for spending | 300 live trades, interval above 0 | 34 live shorts (about 1.9 a session: 300 is about 7 months away) | owner's |

**What those samples can and cannot show (an estimate, labelled as one).** The forward record's session-clustered
standard error at 120 trades is about 1.76 points, which is about 19 points a trade. If that dispersion holds:

| True edge a trade | Trades for the lower bound to clear zero half the time | Four times in five |
|---|---|---|
| +1.1% (the tested figure) | about 800 | about 1,900 |
| +1.8% | about 300 | about 700 |
| +3% | about 110 | about 250 |

At 300 trades the smallest average that clears zero is about +1.8%. So the decision at 300 is well placed to DROP a
fade whose average is at or under zero, and poorly placed to confirm one worth +1.1%. A real edge of the tested size
needs most of a year of alerts to show, at today's pace. Sensitivity: if dispersion fell to 15 points, the first row
becomes about 500 and 1,150.

**In plain terms.** A defensible short edge requires all of: (1) a positive mean with a lower bound above zero, (2)
on trades with no exclusion flag, (3) priced on real quotes, (4) with a measured locate cost in place of the assumed
0.5%, (5) on data no rule was chosen on. Today the record has none of the five. For longs, the record would first
have to stop being negative in every form.

## 8. What this document assumes, and what it did not do

- 0.5% locate is an assumption wherever the execution model is cited. Sensitivity: at 1% subtract half a point from
  every model figure; at 2% the tested mean is negative.
- The stop-out count in the forward file is read as gross at or under -19.99%.
- Today's three trades (ALGS, BNAI, FCUV twice) are not settled and are in no aggregate above.
- The power table uses the forward month's dispersion; a different regime changes it.
- Not done: any split of the held-out file or of the training file; any reading of sealed results (H1, the
  early-alert bands, the scanner sample); any re-run of a registered study; any change to code or configuration.
- Samples L, M and M' overlap (the same November trades in three records) and count once as evidence.
