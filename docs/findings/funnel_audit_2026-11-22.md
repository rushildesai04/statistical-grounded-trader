# Opportunity funnel, decision logic and a bounded research queue (audit phase 3, 2026-11-22)

Owner, 2026-11-22: "Continue the execution audit and frozen forward measurement. Add a separate, controlled
investigation into improvements to trading logic. Do not let 'the existing rule was followed' end an investigation.
Equally, do not assume a more complicated strategy will perform better."

Status: an audit of the complete alert record (2026-11-04 to 11-20: 801 channel alerts, 13 sessions), read-only on the
runner. No rule was changed and no evaluation of any proposed variant was run. The forward cohort that starts
2026-11-23 is untouched. Real money stays locked. Every claim is marked CONFIRMED (read in code or measured),
PLAUSIBLE (consistent with the data, not established) or UNSUPPORTED (no evidence either way).

## 0. Conclusion first
1. **Fix now (done here):** the flaky deploy test; a checkable ledger import; a decision record written at the moment
   of each decision. The long-calls ledger and its paper book are reconciled (section 1).
2. **Still blocking deployment:** real-time full-tape prices and broker-resting stops; a real locate and a broker's
   approval for shorts; a short order that is legal under the price test; any positive forward result.
3. **Leave unchanged:** every gate, entry, exit and size of both alert books. The gates that are deterministic were
   checked on every taken short: no violation (section 5).
4. **No enhancement to the trading rules is supported.** The five questions worth asking are all about what could
   have been EXECUTED or SEEN, not about a cleverer rule (section 7). One is registered as the first experiment.
5. **What would change that:** the registered forward review (30 longs, 50 unflagged shorts), and the first
   experiment's own acceptance rule.

## 1. Foundations (item 1)
| Item | Finding | Status |
|---|---|---|
| 73 against 77 | 105 forward events; 28 predate the receipt record; 77 have a receipt time and were all priced. For 4 of the 77 (BYAH 11-10, NXXT 11-11, ONFO 11-13, MSS 11-20) the fill at the bot's own timing fell outside the rule's own band (price $1 to 50, 09:30 to 15:44, within 5 minutes): they are not events at that timing. Averages by flag use the other 73; "all priced" uses 77 | CONFIRMED |
| Historical import | 53 settled trades written once to `reports/exec_model/ledger.jsonl` on the runner. Checked against the database: 53 lines, 53 distinct trades, none missing, none extra. Checked against the frozen run: every label and every price identical. A second import changes nothing the reader sees (the last line of a trade wins; tested). `exec_ledger.py --check` now prints this reconciliation, and every nightly append ends with it | CONFIRMED, fixed |
| Flaky deploy test | The rate limiter counts requests per clock minute. A test burst that straddled a minute boundary restarted the count and never saw a refusal: about 1 run in 40. The tests now hold the limiter's clock still; the boundary has its own test. The limiter itself is unchanged (a burst across a boundary can pass up to twice the limit; accepted, it protects a database lock) | CONFIRMED, fixed |
| Long-calls ledger against its paper book | Reconciled line by line. (a) A ledger day is a call marked to the NEXT close, so ledger day d is book session d+1. (b) The ledger holds every long call at 1/21 of the book; until the close of 11-20 the paper book used the six-name book's sizing (up to 25% a name, scaled by volatility). (c) Costs: 4 bp a position-day against 5 bp of turnover. Day by day: +0.004% / -0.013%, -0.004% / +0.014%, -1.28% / -2.54% (9 calls: book 90% invested, ledger 43%), -3.16% / -2.96% (14 calls: book 99% invested but small in the three worst names, ledger 4.76% in each). Totals -4.40% and -5.42%. Recomputing the book's last day from its weights and the ledger's own call results gives -2.96%: the two agree on every price | CONFIRMED |
| ... what remains different | From session 11-23 both hold 1/21 a call, so they should agree within the cost convention, except on a batch day (the ledger counts it, the book does not trade it) and a half day (the book is flat, the ledger counts it). None occurred in the window | PLAUSIBLE, to be watched |
| Execution assumptions still in the baseline | 1 s order latency; top-of-book size only; stop triggered by the last sale; halts read from the tape; borrow from a public list; the price test computed, not read from an exchange; daily books filled at a close they could not always order (bounded: 16 to 21 bp of book value a session at worst) | labelled, not resolved |

## 2. The full funnel (item 2)
Every alert the channel posted, not only the trades. "Would-have" = the tested short on official bars at the tested
timing, a measurement the nightly job already keeps; it is bar-priced, not re-priced at quotes.

**Shorts**

| Stage | Alerts | Why they stopped here | Would-have average (scored) |
|---|---|---|---|
| Posted by the channel | 801 on 216 stock-days | | |
| Not a tested kind | 261 | news post with no news flag 113, "halted" 69, after-hours high 59, pre-market high 20 | not scored |
| A tested kind | 540 | | |
| ... no row: the stock already had a live alert that day | 117 | one trade per stock per day (115 plain repeats, 1 repeat 5 s later, 1 while the first was still pending) | not scored |
| ... no row, unexplained | 1 | VRAX 11-04 11:27 ET, the first live day; received (stored 29 s after posting) but no row | - |
| A row in the book | 422 | | |
| ... outside 09:30 to 15:44 | 169 | pre-market and late alerts | -3.1% (14) |
| ... company-news rule | 115 | | -2.8% (101) |
| ... price outside $1 to 50 | 57 | read at the entry | -1.1% (56) |
| ... alerted in the prior 7 days | 12 | | +11.6% (10) |
| ... no print within 5 minutes | 12 | halted or silent | +1.1% (7) |
| ... book switched off | 12 | 11-13 to 11-16 | +4.2% (12) |
| ... no shares listed | 8 | from 11-17 | +8.5% (8) |
| ... beta 3 or more | 4 | | +15.6% (4) |
| ... approval not given in time | 4 | first days, approval mode | not scored |
| TRADED | 29 | | official -1.7%; re-priced -1.7% |

Receipt: median 26 s after posting; 3 alerts over 60 s; none over 300 s; no alert failed to parse or to reach a book.

**Longs** (from the book's first day, 11-10): 82 first-qualifying alerts of a stock-day.

| Outcome | Alerts | Meaning |
|---|---|---|
| No setup formed in the 60-minute watch | 37 | "none" |
| Bought | 24 | |
| Watched while the book was off | 3 | "seen" texts |
| Fill seen more than 5 minutes late | 2 | NOMA, TDTH 11-16: the feed's fill candle arrived late; not bought |
| No row | 16 | 8 before the book's first minute on 11-10, 8 while it was switched off (11-13 afternoon, 11-16 morning) |
| Later alerts of the same stock that day, never watched | 87 | the rule watches the first qualifying alert only |

**The questions asked of it**

| Question | Answer | Status |
|---|---|---|
| Good opportunities missed by late or wrong evaluation? | Shorts: none by lateness (no alert arrived after its 5 minutes). Longs: 2 fills seen too late on the thin feed; 16 alerts while the book was off or not yet live. Whether any were "good" is not known and is not assumed | CONFIRMED counts |
| Invalid opportunities admitted? | Shorts: 14 of 29 taken carried a flag (no listed shares before the rule existed, or the price test, which the bot does not check). Longs: 6 of 24 were booked when the price was already at or under the stop; at least 2 setups (SVRE's base, JMIA's break, both 11-20) existed on the thin feed and not on the full tape | CONFIRMED |
| Duplicate or contradictory signals? | 97% of stock-days have more than one alert (4 to 9 on 65, ten or more on 8). On 7 stock-days the books took a short AND a long; on 6 they were open at the same time (all before the rule of 11-20 that now blocks it) | CONFIRMED |
| Setups valid only after the first alert? | The long book watches 60 minutes from the first qualifying alert; 87 later alerts started no watch. For shorts, fading the channel's later alerts was tested and lost (-3.19%, 402 events) | CONFIRMED / tested |
| Setups that expired before evaluation? | The 2 "missed" longs. No short | CONFIRMED |
| Correctly rejected? | By their would-have: outside hours, news and price, yes (negative). Repeat, beta, no-shares and off rows are positive on 4 to 12 alerts each: too few to say, and the borrow rows could not have been traded at all | PLAUSIBLE / too few |
| Data insufficient to decide? | 12 alerts with no print; every long decision (the feed is under 1% of the volume); every short's borrow (a list) and price-test status (not checked live) | CONFIRMED |

## 3. The books as state machines (item 3)
**Short.** RECEIVED -> (not a tested kind: dropped) | DUPLICATE (stock already live today) | FILTERED (hours, news
alert, prior-7-day alert) | SKIPPED (late, paused, caps) | PENDING. PENDING, read every 10 s until the tested minute
+ 5 minutes: company news found -> FILTERED; beta 3+ or unknown -> FILTERED; no print in the tested minute -> wait,
then SKIPPED; price outside $1 to 50 -> FILTERED; no shares listed or list unknown -> FILTERED; book off -> FILTERED;
else OPEN at that print. OPEN, read every 10 s: last print at or over entry x 1.2 -> CLOSED (stop); 60 minutes or the
close -> CLOSED; the owner's NO -> CLOSED; the official tape, 16 minutes late, shows the stop -> CLOSED or corrected.
Nightly: SETTLED on official bars, and re-priced at quotes in the ledger.

**Long.** WATCHING (first qualifying alert of a stock-day, 60 minutes + grace) -> NONE (no setup) | setup complete:
book off -> SEEN; cost over 1% -> COSTLY; our short open -> BLOCKED; target cannot beat costs -> skipped, keep
watching; fill seen over 5 minutes late -> MISSED; else OPEN (bought at the break candle's close since 11-20). OPEN,
read every 15 s on closed feed candles: stop | target | 30 minutes | (old rules) red candle -> CLOSED; the official
tape, 16 minutes late, shows a stop first -> CLOSED or corrected. Nightly as above.

States that do NOT exist: entry pending at a broker, partially filled, exit pending, and "blocked because data is
unsafe" for an OPEN trade (a feed that goes silent mid-trade changes nothing; the trade waits for its timer).

| Distinction | In the logic today? | Status |
|---|---|---|
| Initial breakout against a failed breakout | Partly: a break must CLOSE above the base and above VWAP. A break that fails afterwards is simply the stop | CONFIRMED |
| Pullback holding against continued selling | No. For shorts "wait for a breakdown and a failed bounce" was tested and lost | CONFIRMED / tested |
| Tight base against a wide, unstable range | Yes (10 minutes, range at most 4%), but measured on the thin feed: a base can exist there and not on the tape | CONFIRMED, implemented on insufficient data |
| First spike against repeated attempts | Shorts: one trade a stock a day, none after an alert in the prior 7 days. Longs: first qualifying alert only | CONFIRMED |
| Normal session against a halt reopening | Shorts trade a "resumed" alert like any other, entering only on a print inside the tested minute; no halt state during a hold. The long halt-reopen setups were retired | CONFIRMED |
| Executable liquidity against an unavailable price | No live check: no real-time quote, borrow from a list, no price test. Only known the night after, in the ledger's flags | CONFIRMED gap |

## 4. Three decisions with only what the bot knew (item 4)
| Event | What had arrived | Decision and reason | Reasonable under its rules? | What it could not know |
|---|---|---|---|---|
| ZJK short 11-20 | alert 14.6 s after posting; list of 250,000 shares read 12 minutes earlier; no news article; one-exchange print $1.37 at 09:54:16 | short at that print: every gate passed | Yes | the list predated the halt's end; a second broker said not shortable; the price test was in force from the prior day (the bot does not compute it) |
| SVRE long 11-20 | feed candles showing 10 quiet minutes and a candle closing above them | buy: base break complete | Yes, on its data | on the full tape those minutes were not a base |
| RETO long 11-12 | a feed candle that filled the setup at $8.82; the bot saw it once the next candle closed, 70 s later | booked at $8.82 | Yes, as coded then | the ask was $9.50 by then; the booked price no longer existed (fixed 11-20: bought at the candle's close) |

In all three the rule was followed. In all three the DATA, not the rule, is what failed. None used a later price.

## 5. Abstaining, checked directly (item 7)
Deterministic requirements, verified on all 29 taken shorts and 24 taken longs from the stored facts of each entry:

| Requirement | Violations |
|---|---|
| price $1 to 50 at the entry; entry 09:30 to 15:44; within 5 minutes of the alert | 0 |
| no company-news article (from 11-10); beta under 3 (from 11-10); shares listed (from 11-17) | 0 (unknown beta: 3 on 11-10, none since; unknown is "no short" from 11-21) |
| no alert in the prior 7 days (from 11-13) | 0 (GMM 11-18 had earlier rows, a no-print and an outside-hours one: not events, correctly not counted) |
| one live row a stock a day; off switches | 0 |
| long: stop under the entry; no long while our short is open (from 11-20) | 0 |

Not blocked because nothing checks it: stale or silent data during an open trade; spread against risk; the price
test; a real locate. These are safety requirements, not predictions: each needs data the system does not have live.

## 6. Case distinctions (item 5) and scores (item 8)
One line each. "Earlier" = already tested on the research files (playbook sections 3 and 4); those are not re-run.

| Distinction | Mechanism | Known before entry? | Evidence here | Earlier | Further work justified? |
|---|---|---|---|---|---|
| Spread against planned risk (longs) | a 3% stop cannot survive a 1% spread and a stop that slips 3% | only with real-time quotes | entry spread median 0.5%, stop distance median 2.9%, stop minute's low 3.1% under the stop | cost cap (11-20) | measure in the ledger; no rule (H3) |
| Volatility before the alert (shorts) | hype reverts, calm drift does not | yes | not looked at here | jumpiest fifth +3.6% / +4.9%, calmest negative; two forward shadows running | no: already running |
| Alert latency / place in the move | a late alert is a spent move | yes | NOT READ: registered forward test, sealed until 300 alerts | - | no: registered |
| Time of day | later fades did better | yes | too few | +0.6% before 11:00 to +2.3% after 14:00 | no |
| Halt reopening ("resumed") | auction price, widest spreads, least certain borrow | yes | 5 of 29 shorts: the 2 best and 2 of the worst | halts right after an alert lose (live-only, fixed 11-09) | report as its own class (H4) |
| Company catalyst | news gives buyers a reason | yes (one feed) | gate holds on its would-have | news fades break even | no |
| Extension from the day's levels | - | yes | not looked at | chart-read exits lose; no entry filter kept | no |
| Market and sector | - | yes | 13 sessions, one sharp down week | not tested for the alert books | UNSUPPORTED: no sample |
| First against repeated alert | re-alerts mark a sustained run | yes | 87 later alerts unwatched (longs) | fading later alerts lost; repeats within 7 days lose | only as a shadow, last (H5) |
| Borrow and the price test | an order that cannot be sent is not a trade | flag computable, locate not | the live split; not in the backtest (registered check: NOT SHOWN) | - | yes: H1 |

Scores. Probability of profit, expected return, loss, eligibility and data quality are five different things, and only
two exist: the average of a KIND of trade (the money score) and eligibility (the gates). Two registered tests found
nothing that ranks one trade above another of its kind; the stored 0-to-99 number had a correlation of 0.01 with
results. Nothing new ranks trades here. Data and execution quality now exist as FLAGS in the nightly ledger. Until a
ranking is shown on new data the honest display is the kind's average with its sample size, and "insufficient
evidence" for anything finer.

## 7. Research queue: five questions, none of them a new trading rule (items 6 and 9)
Ranked by usefulness, strength of the reason, and cost. Entry and exit are never varied separately: every variant
keeps the frozen stop, target and hold and is reported per alert AND per filled trade, with fill rate and tail loss.

| # | Question | Problem it comes from | For | Against | Earlier test | Cost |
|---|---|---|---|---|---|---|
| H1 | Can a short under the price test be sold with a limit at the offer, and what is it worth per alert? | 14 of 29 shorts and 36 of 73 forward events are "unresolved": the baseline cannot price a third of the book | a legal mechanism, known before entry, affects 35% of events in both samples | adverse selection: an offer fills when the stock runs up; a limit 2 to 10% above the print was tested and rejected | `prereg_fade_limit_entry_2026-11-17` (a different order: above the market, all alerts) | low: quotes and prints already read |
| H2 | Did the long's base and break exist on the full tape? | phantom bases and feed-only breaks | direct test of whether the losses are data or rule | 15-minute delay: can only be measured after the fact, cannot be traded on | none | low |
| H3 | Spread and stop slippage against planned risk, per long | stops 3% wide in stocks that move 3% a minute | arithmetic | needs real-time quotes to act on | cost cap | none: already in the ledger |
| H4 | Halt reopenings as their own class of short | extreme outcomes, least reliable execution | 74 such alerts in 13 sessions | 5 trades | halt audit 11-09 | none: a label |
| H5 | What the frozen rules would do on a stock's later alerts | 87 unwatched later alerts | - | fading later alerts lost; more trades of a losing rule | `prereg_fade_later_alerts_2026-11-06` | medium; only if H2 shows the rule is not the problem |

Not in the queue, because already rejected or already running: confirmation entries, trailing and partial exits,
breakeven stops, longer holds, time-of-day and price sizing, volatility sizing, the repeat gate's day-after split.

**First experiment: H1**, registered in `prereg_short_price_test_limit_2026-11-22.md` before any of its outcomes is
computed. It is an execution variant with its own identifier (`short-ssr-limit-v1`), its own ledger file and its own
lines in the decision record. It reads the same alerts as the baseline and writes nothing to it.

## 8. Decision observability (item 11)
Implemented: table `decisions`, one line per state change of either alert book, written by the book at the moment it
decides: time, book, alert or trade, state before and after, the reason, the facts read at that moment (posting and
receipt times, seconds of delay, the price, the beta or news reading, stop and target, size), and the rule version in
force. A duplicate alert, which used to leave no trace, now leaves a line. It cannot cost a trade (tested with the
table removed). Not yet on the record: alerts of a kind the books do not trade; quote age at a decision (there is no
real-time quote to age); the daily books. Next step, proposed: the trade review reads this record instead of writing
a narrative from the chart.

## 9. Review of the queue (item 10)
An independent reviewer was asked to attack H1 and the queue for look-ahead, unrealistic execution, overfitting,
small and correlated samples, dependence on outliers, whether simpler logic does as well, and whether H1 addresses
the diagnosed cause. Its points and what was done with each are recorded at the end of the registration document.
Agreement from a reviewer is not evidence that anything makes money.
