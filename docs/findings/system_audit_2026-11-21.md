# System audit: what the bot does, what its record assumes, what is supported (2026-11-21)

Owner, 2026-11-21: a full audit brief ("understand, audit, debug, and improve the actual system, not merely explain away
its losses, defend existing rules, invent arbitrary tests, or rebuild everything").

Status: an audit of the LIVE path and its record. Code read in this repo at main `1bb7348`; facts from the runner's
database and stored official bars, read-only, on Saturday 2026-11-21 (13 sessions of alerts, 2026-11-04 to 11-20).
No registered study was re-run and no held-out file was opened. Numbers are per trade after modelled costs unless
stated. Everything here is PAPER: no order has ever been sent to a broker by any book.

## 0. The five answers

| Question | Answer |
|---|---|
| Is the software behaving correctly? | It follows its written rules. No trade was found that broke a rule or an off switch. Five defects were found in the RECORD and the REPORTING (sections 2 and 4), fixed in this change. |
| Are the reported trades executable? | Not as booked. Shorts: on 9 of 29 the alert reached the bot after the minute whose opening price the official result uses. Longs: every official stop exit is booked at exactly the stop price; the stop minute's low was 3.1% lower on average. Borrow is a list, not a locate. |
| Are the scores meaningful? | Only as an average of a KIND of trade. Nothing ranks one trade above another. The stored 0 to 99 "confidence" had no relation to results (correlation 0.01 on 22 trades). The text's "Win chance 57%" was a backtest figure the live record is not meeting. |
| Is there credible evidence of positive net expectancy? | No, for no book. Shorts: positive in the backtest, not confirmed live (forward record in alarm since 11-13; 29 paper shorts average -1.7% with a standard error of 3.1). Longs: negative in the backtest AND live (-2.5%, standard error 0.9, 24 trades, at best-case fills). Daily model books: 5 to 15 sessions, all down. |
| What remains uncertain? | The size and sign of the entry-timing effect on shorts (unmeasurable on 29 trades); real stop fills in thin stocks (needs prints); whether the short backtest survives the real alert delay (needs a registered re-score); everything that needs a real broker (locates, rejects, partial fills, halts). |

## 1. What the system actually does (A)

Market data -> alert -> eligibility -> setup monitoring -> entry -> sizing -> order -> fill -> monitoring -> exit ->
reconciliation -> reporting. One process on the runner (`scripts/fade_live.py run`), eight loops.

| Stage | Code | What it reads | How often / how late | On failure | Replay vs paper vs real |
|---|---|---|---|---|---|
| Alert | a third party's Telegram channel; `fade_live.py:603` push handler and `:608` 15 s poll (`fast/chan_poll.py`) | channel messages, parsed by `crowd/telegram_parser.py` | received a median 22 to 40 s after posting per session to 11-18, 12 to 14 s on 11-19/20 (poll); 90th percentile 46 to 58 s throughout; worst 115 s | per-alert try/except, audit row `alert_failed`; an alert older than 5 min is skipped ("alert received late") | the backtest assumes the alert is known 10 s after posting |
| Eligibility (short) | `fast/fade.py:237 on_alert`, `:394 _enter` | alert type, session, 09:30 to 15:44 ET, news article flag, prior-7-day alert, beta, price $1 to 50, IBKR lendable list, off flag | at the alert, then every 10 s until entry | a blocked alert is stored with its reason; unknown borrow = no trade; unknown news or beta does NOT block | backtest: same filters except borrow and beta lookups are live-only |
| Entry (short) | `fade.py:412` | the latest trade on ONE exchange (IEX, about 2.5% of market volume) at or after the tested minute's start | loop every 10 s (`TICK`); decision a median 10 s, 90th percentile 33 s after that minute opened | no print within 5 min -> skipped | official result = the full tape's open of that minute, whatever the bot saw or when |
| Setup monitoring (long) | `fast/long_book.py tick`, `fast/patterns.py:214 base_break` | CLOSED 1-minute candles of the same single exchange | every 15 s; a base break must come within 60 min of the alert; base >= 10 min, range <= 4% | a quiet feed has no candle: the setup waits; after the window the row is marked "none" | official replay uses the full tape from the break candle's minute |
| Entry (long) | `long_book.py at_close` (since 2026-11-20) | the break candle's close | within 90 s of the candle close, else the old path | cost above 1%, our own short open, or off flag -> watched, not bought | paper price = feed candle close; official = full-tape open of the next minute |
| Sizing | `fade.py:598 trade_usd`, `:632 drift_scale`; long: risk-sized, test size $500 | book value, drift guard | at entry | drift alarm cuts new shorts to 25% | no buying-power or margin model |
| Order / fill | none | - | - | - | **no order exists**: a row in `fade_trades` / `long_trades` is the "fill" |
| Monitoring / exit (short) | `fade.py:498 _exit`; `fast/tape_check.py` | latest single-exchange print every 10 s; official 1-minute bars once 16 min old, every 30 s | stop +20% on the live estimate, 60 min, or the close; owner's NO | a stop the thin feed never prints is found on the full tape about 16 min later and corrected by text | the stop is simulated; nothing rests at a broker |
| Monitoring / exit (long) | `long_book.py tick` | closed single-exchange candles every 15 s | stop under the base, target 2x risk, 30 min | no full-tape check during the trade | official stop = the stop price exactly (or the minute's open on a gap) |
| Reconciliation | `fade_live.py:962 cmd_settle`, 20:40 ET | full-tape 1-minute bars (free once 15 min old) | nightly | bars missing: retried for 3 days | this is the "official" result on the site |
| Reporting | texts (`fast/fade_texts.py`), site (`ops/charts.py`, `scripts/trade_charts.py`), nightly summary (`ops/digest.py`), AI review (`ai/trade_review.py`) | the database | texts at entry and exit; site every 10 min, candles to 16 min ago | - | - |

Direct answers to the brief's questions:
- An alert does not trade at once. A short waits for the first print in the first minute starting at or after alert + 10 s.
  A long watches up to 60 minutes for a base break and trades only if one forms (37 of 66 long rows ended "none").
- Candles are not processed as a stream. The long book re-reads the feed's closed candles every 15 s; a minute with no
  trade on that one exchange has no candle. Exits are checked every 10 s (shorts) or 15 s (longs), not on every print.
- Stops are managed locally and simulated. Nothing rests at a broker.
- A restart keeps open rows (they are in the database) and resumes; deploys are refused while a trade is open. A channel
  outage longer than 5 minutes turns alerts into "received late" skips.
- Two books on one stock: a long is not bought while our own short on that stock is open (rule of 11-20). The daily
  model books hold large caps only and never meet the alert books.
- Off switches (`fade_off`, `long_off`): checked at entry. No trade was booked while a switch was set (verified on every
  row in both tables). One gap: the owner's decision of 11-12 at night went live with the deploy at 11:01 ET on 11-13;
  one short (FCUV, 09:58 ET, +11.6%) was taken in between by the old code.
- The money score (GO / NO-GO) is worked out when the site page is built, from today's record. No book reads it. Both
  alert books have traded while it said NO-GO, by the owner's word of 11-18 ("everything should be on and trading").
- Times: stored in UTC, shown in Eastern through the tz database; the runner's cron runs in Eastern. The system has
  not yet lived through a clock change (the next is 2027-03-14): not observed, only read in code.

Not traced end to end in this audit: the daily model books' own decision path, the scanner (paused), the real-money
pilot code (never enabled), the broker connectors.

## 2. One trade end to end: ZJK short, 2026-11-20 (C)

| Eastern time | Event | Source |
|---|---|---|
| 09:41:40 | IBKR lendable list read: 250,000 shares, fee 12.7% a year. Alpaca: not shortable | `fade_trades.ib_snap`, `alp_shortable` |
| 09:53:04 | channel posts "resumed" (trading resumed after a halt) | `posted_at` |
| 09:53:18.6 | bot receives it (14.6 s) and stores the row | `created_at` |
| 09:53:22 | news, filings, social, Reddit read: no company-news article | `ctx_at` |
| 09:54:00 | the tested entry minute opens. Full tape open: $1.4101 | `entry_ts`, `entry` |
| 09:54:16.5 | first loop pass with a print in that minute: $1.37 on the single exchange. Booked; text sent 09:54:18 | `est_entry_at`, audit `fade_text` |
| 09:55:00 | the next full-tape minute opens at $1.21 | stored bars |
| 10:54:24.8 | 60-minute timer: estimate $0.8888, text sent | `est_exit_at` |
| 20:40 | official: sold $1.4101, covered $0.92, +33.0% after 1.25% costs | `net` |

What it shows. The rule was followed. The official price ($1.4101) is from 16 seconds before the bot decided; the bot
saw $1.37; one minute later the stock was $1.21. The borrow fact was 12 minutes old and from before the halt ended,
and the second broker said not shortable. It is the book's best trade, on a halt reopening, with the weakest claim to
being a trade a real account could have made at that price. Halt-reopening alerts are 5 of the 29 shorts and include
the two best (+33.0%, +25.6%) and two of the worst (-21.4%, -21.3%); the other 24 average about -3.2%.

## 3. The record, measured (C, E)

### 3.1 Shorts (29 settled, 2026-11-04 to 11-20)
| | trades | average | wins | avg win | avg loss | profit factor |
|---|---|---|---|---|---|---|
| all | 29 | -1.71% (standard error 3.08) | 45% | +13.4% | -14.0% | 0.78 |
| to 11-12 (full size) | 23 | -4.95% | 35% | | | 0.44 |
| from 11-16 (quarter size) | 5 | +10.6% | 80% | | | |

Worst -21.8% (five trades at the stop), best +33.0%. One standard error is 3 points: the sample cannot tell +4% from -8%.

Entry timing, re-scored read-only with the book's own function (`fade_live.at_live_entry`) on the stored official bars:

| the short is sold at | average of the same 29 | wins |
|---|---|---|
| the official record: first minute at or after alert + 10 s | -1.71% | 45% |
| the first minute after the bot RECEIVED the alert | -0.30% | 52% |
| the first minute after the bot DECIDED (conservative: a real order fills inside the decision's minute) | -0.83% | 52% |

On 9 of 29 the alert arrived after the official minute had opened. Paired difference to the official: +0.9 and +1.4
points with a standard error of 1.7: not different from zero; one trade (RETO 11-12: -21% official, +26% a minute
later) makes most of it. The finding is not that the record is too rosy or too harsh; it is that on about a third of
the trades the official price could not have been had, and the direction of the error is unknown.

Live label against official result: FISN 11-09 was texted as a stop (the single-exchange estimate) and is officially
a 60-minute exit; MPU 11-17 was held to its timer while the full tape had crossed the stop 9 minutes in (the reason
the full-tape check exists since 11-17).

Borrow: 8 of the 29 had no lendable shares at all (before the rule of 11-16; none since). 16 of 29 were lendable at
IBKR while Alpaca said not shortable. The list is 6 to 31 minutes old at entry (median 14). Fees: median 30% a year,
a tenth above 336%. IBKR states its list is "indicative only" and that every short order is subject to its approval.

### 3.2 Longs (24 settled, 2026-11-10 to 11-20)
| | trades | average | wins | avg win | avg loss | profit factor |
|---|---|---|---|---|---|---|
| all | 24 | -2.54% (standard error 0.94) | 25% | +2.8% | -4.3% | 0.22 |
| base break (live rule since 11-19) | 3 | -4.5% | 0 of 3 | | | |

This is significantly below zero and matches the backtest (-2.1% for the old setups, -0.7% for the base break): the
long book loses because its rule loses, not because of bad luck or one bad week.

Stop fills. 16 of 24 exits are stops, all booked at exactly the stop price. On the 15 with stored bars:

| the stop exit is priced at | average of those 15 |
|---|---|
| the stop price (the official record) | -4.9% |
| the close of the stop's minute | -5.9% |
| the low of the stop's minute | -7.8% |

The official long record is the best case. The real fill of a stop order lies between the first and the third row; it
cannot be placed more precisely without the prints.

Live label against official result: 7 of 24 differ. ADXN and XLAB (11-12) were texted as targets hit and are official
stop losses; RETO (11-12) was texted as a stop and is an official target; OMH and MRKR exited "red" officially; two
were scored "bought at the text". The cause is the single-exchange feed, not the rule.

Buy timing (already fixed 11-20): the buy was booked and texted 64 to 84 s after its price (10th to 90th percentile).

### 3.3 Scores
- `conf` (0 to 99, stored at entry): 100 x the bootstrap chance that the AVERAGE of a backtest cell is above zero. 18 of
  the 22 scored shorts sit at 95 to 99: 50% won, average -1.9%. Correlation with the result: 0.01. It is not shown in
  texts since 11-10; it is still stored.
- Text line "Win chance 57%": the backtest win rate of the cell (no news article, before 11:00), shown while the forward
  record of every alert has been in alarm since 11-13 (42% wins against 57% tested at the last alarm, 11-16).
- Money score: the average of the KIND of trade times the dollars, GO when that average is positive. For shorts it
  switches to the forward record while the guard is in alarm; the text did not. It is computed at page build.
- AI review verdicts (58 reviews): execution issue 18, rule loss 16, rule win 16, unclear 8. Its instructions stated
  "no-news fades earn about +1.7% per trade" as a tested fact and defined a rule loss as "variance".

### 3.4 The alert stream (it is a third party's channel; we do not compute the alerts)
801 alerts in 13 sessions on 217 stock-days: 3.7 alerts per stock-day, up to 20 on one. By type: news 221, new high 144,
volume increasing 109, resumed 74, halted 69, after-hours high 59, others 125. Of the 422 that reached the short book:
169 outside 09:30 to 15:44, 115 news, 16 under $1, 12 repeats of the prior week, 12 with no print in 5 minutes, 8 no
borrow, 4 high beta; 29 traded. Where in its move the stock was at the alert (247 labelled): late 120, middle 65,
early 62; the results by that label are a registered forward test and are not read before 300.
"No company news" means: no article from one news feed (Benzinga through Alpaca) tagging at most two symbols since the
prior 16:00 ET. Roundup articles naming many tickers are correctly not counted as company news. A press release or a
post that the feed does not carry reads as "none".

### 3.5 Daily model books (paper, through 11-20)
megacap -0.6% (15 sessions), megacap_ext -3.1% (9), megacap_long -5.4% (5; its deciding ledger -4.4% against a
registered -5% stop), megacap_overlay -11.0% (10), 60/40 yardstick -5.0% (14). Too short to judge; the market fell.

## 4. Findings, by priority (B)

| # | Symptom | Kind | Evidence | Impact | Action |
|---|---|---|---|---|---|
| 1 | Short official entry is a price from before the bot knew of the alert on 9 of 29 | execution assumption (also in the backtest: 10 s reaction) | 3.1; receipt delay table in section 1 | sign unknown, +-1.7 points a trade of noise; the backtest's entry is not this system's entry | FIXED as a record: nightly `exec_check` stores the same short from the bot's own decision, shown in the summary. PROPOSED: a registered re-score of the forward record at real receipt times |
| 2 | Long stop exits booked at the stop price | fill model | 3.2: the stop minute's low is 3.1% lower | the long record (-2.5%) is the best case; true figure between -2.5% and about -4.4% | FIXED as a record: nightly `exec_check` stores the stop minute's low. PROPOSED: prints-based stop fills |
| 3 | Longs lose | strategy performance | 3.2; backtest negative too | about -$12 a trade at the $500 test size | none in code. No rule change is supported by any test this month. Stays paper |
| 4 | Text says "Win chance 57%" while live says otherwise | reporting | 3.3 | reads as this trade's odds | FIXED: "Past win rate", plus a warning line with the forward record while its guard is in alarm |
| 5 | AI reviewer told the short rule is proven and losses are variance | reporting | `ai/trade_review.py` RULES | 16 "rule loss" labels rest on it | FIXED: instructions state the backtest is not confirmed live; ten issue kinds instead of three |
| 6 | Site shows the live exit label and time on an official result | reporting | 2 shorts, 7 of 24 longs | a card can say "target" on an official stop loss | FIXED: settled cards show the official exit; the texted one beside it when different |
| 7 | NO-GO shown beside a trade that was taken | reporting / by owner decision | score is advice, built at page time | reads as a rule being broken | FIXED: the card says the verdict is advice and as of the page |
| 8 | Stops are simulated on 2.5% of the volume | risk control / data | MPU 11-17; 7 long label mismatches | a stop can be crossed and stay open up to 16 min (shorts) or until the feed shows it (longs) | needs real-time full-tape data (owner's pending broker data). Not fixable in code on the free feed |
| 9 | Borrow is a list 6 to 31 min old; brokers disagree on 16 of 29 | eligibility | 3.1 | some paper shorts were not available | record a real locate once a broker is connected. Until then the paper short record is an upper bound on availability |
| 10 | An owner decision took 12 hours to be enforced | operations | FCUV 11-13 | one trade | switches are flags since 11-13: effective in seconds. No code change |
| 11 | "No company news" is one feed's silence | data | 3.4 | unknown | wording kept ("none found"); a second source is a cost decision |
| 12 | A failed news or beta lookup does not block a short (borrow unknown does) | eligibility, fail-open | `fade.py:524`, `:538`: unknown "never blocks" | news: 0 trades so far; beta: 3 trades, all on 11-10, the rule's first day | OWNER DECISION (it changes which trades are taken): make unknown news and beta a "no trade", like unknown borrow |

Not defects, checked: off switches hold; roundup articles are not treated as company news; a halted stock's last
pre-halt print is not used as an entry (only a print inside the tested minute counts, `fade.py:412`, since 11-09); a
same-minute stop and target on a long is booked as the stop; duplicates of an alert on the same stock and day do not
open a second trade.

## 5. Changes in this pull request (D)

| Change | Files | Kind |
|---|---|---|
| Nightly execution check: each settled short re-scored from the first official minute after the bot's own decision (`dec_entry`, `dec_net`); each long stop exit's minute low (`stop_low`); one line in the nightly summary | `src/sgt/fast/exec_check.py` (new), `src/sgt/ops/store.py` (4 new nullable columns), `scripts/fade_live.py` (settle step), `src/sgt/ops/digest.py` | measurement; touches no book value, total or trade |
| Settled cards show the official exit reason and minute; the texted label is kept beside it when it differed | `src/sgt/ops/charts.py`, `scripts/trade_charts.py`, `src/sgt/ops/templates/trade_charts.html` | reporting fix |
| "Win chance" -> "Past win rate" (short and long texts); a warning line with the forward record while the drift guard is in alarm | `src/sgt/fast/fade_texts.py`, `src/sgt/fast/fade.py`, `src/sgt/fast/long_book.py` | reporting fix; no new text type, one more line in the short's entry text only during an alarm |
| AI review: the backtest is not stated as a live fact; "rule loss" is not "variance"; ten issue kinds | `src/sgt/ai/trade_review.py` | reporting fix; old stored reviews stay valid |
| Money score note: advice, as of the page | `src/sgt/ops/templates/trade_charts.html` | reporting fix |

No entry rule, exit rule, size, gate, flag or broker setting is changed. Rollback: revert the commit; the four columns
are nullable and ignored by older code.

## 6. Validation (E)

- Tests: 2,116 before, 2,141 after (25 new), all passing; lint clean. Mutation check of the new logic: 10 of 11 code
  changes caught by the tests; the one left is equivalent (an unsettled short has no official price, so the guard it
  removes cannot change a result).
- The execution check's first live run is the settle after this is deployed: it fills the 29 shorts and 16 long stops
  inside its 14-day window (older rows stay empty) and should reproduce 3.1 and 3.2.
- One independent code review of the change (a separate reviewer told to falsify it): graded sound, no blocking
  defect; two of its three minor notes were applied (the warning line no longer names only the win rate; the summary
  line has its own failure label). The third stands: the first nightly run fetches bars for up to 14 days of trades.
- NOT validated: nothing in this audit tests whether any rule has an edge. Real fills (locates, rejects, partial fills,
  queue position, spread on a halt reopening) cannot be tested without a broker. The short backtest's code was not
  re-read line by line here (two independent checks on 11-09 and 11-12 found no bias in it); its 10-second reaction
  assumption is the new doubt.

Review passes on the conclusions:
- Implementation: the rule in code is the rule in the plan for both alert books; the differences found are in the record.
- Execution: section 3. The record assumes prices the bot could not always have had (shorts) or a perfect stop (longs).
- Statistics: 29 and 24 trades. The longs' loss is clear (2.7 standard errors, and predicted by the backtest). Nothing
  else is. The re-scored short figures above are a description of 29 trades, not a result.
- Trading logic: the short's best and worst trades are halt reopenings, where spreads are widest, the reopening is an
  auction and borrow is least certain; that is where the paper record deserves the least trust.
- Operations: one process, no broker, stops in software; safe for paper, not a design for real money as it stands.

## 7. Strategy status (F)

| Book | Backtest | Live paper | Status |
|---|---|---|---|
| Alert fade (short) | positive (+1.1% held-out, 764 trades) | forward record in alarm; 29 trades -1.7% +- 3.1 | experimental. Not confirmed. No support for real money |
| Alert long | negative | negative, clearly | no edge on any evidence. Paper only; its losses are the expected result of the rule |
| Long calls on large caps (`megacap_long`) | positive on a hindsight universe; failed on unseen stocks | ledger -4.4% in 4 sessions, stop at -5% | registered forward test running; its own rule decides |
| megacap, megacap_ext, overlay | measurement books | down | insufficient evidence |
| Scanner | failed its test | paused | closed |

Real-money orders stay locked at every broker. Nothing here supports unlocking them.

## 8. Measurement plan (G)

| Measure | Where | Decides | When |
|---|---|---|---|
| Short from the bot's own decision against the official | nightly summary line "execution check" | whether the official short record is re-based to the bot's own timing | at 50 shorts with both figures: re-base if the paired difference is beyond 2 standard errors either way; otherwise keep both shown |
| Share of alerts received after their official minute | same line | whether "alert + 10 s" is this system's rule at all | at 50: above 20% -> the backtest is re-scored at measured delays under a registered plan (training and held-out have no receipt times, so the delay is an assumption stated in advance) |
| Long stop minute's low | same line | how much worse than the stop price a stop fill must be assumed | at 30 stop exits: fetch the prints of each stop minute and price the stop as the first print after the trigger; if more than 1 point worse on average, the long cost model is raised by it |
| Fade forward record | `reports/fade_challengers`, drift guard | whether the short has an edge live | at 300 alerts (registered, amendment F1): confidence interval above zero keeps it, otherwise it ends |
| Long-calls ledger | `megacap_long` | the registered forward test | -5% stop or 2027-04-30 |
| Real-time full tape | owner's pending broker market data | whether stops, bases and breaks are seen as they happen | when the app exists |
| Real locate at entry | first connected broker | whether a paper short was available | from the first connected session |

## 9. External facts used

- The free data plan gives real-time trades of one exchange, about 2.5% of market volume, and refuses the most recent
  15 minutes of the full tape: [Alpaca, About Market Data API](https://docs.alpaca.markets/docs/about-market-data-api).
- A stop order becomes a market order at its trigger; the stop price is not the execution price:
  [SEC Investor.gov bulletin on stop orders](https://www.investor.gov/additional-resources/news-alerts/alerts-bulletins/investor-bulletin-stop-stop-limit-trailing-stop),
  [FINRA Regulatory Notice 16-19](https://www.finra.org/rules-guidance/notices/16-19).
- A limit-up/limit-down pause lasts five minutes and trading reopens with an auction on the listing exchange; bands are
  20% for stocks from $0.75 to $3: [Cboe LULD FAQ](https://cdn.cboe.com/resources/membership/LIMIT-UP-LIMIT-DOWN-FAQS.pdf),
  [Investor.gov, measures to address market volatility](https://www.investor.gov/news-alerts/investor-bulletins/measures-address-market-volatility).
- A broker's list of lendable shares is indicative and every short order is subject to approval:
  [Interactive Brokers, short securities availability](https://www.interactivebrokers.com/en/trading/short-securities-availability.php).
- After a 10% fall from the prior close a short sale may only be made above the best bid for the rest of that day and
  the next (relevant to shorting a stock that reopens DOWN after a halt; not modelled here):
  [SEC, Rule 201 FAQ](https://www.sec.gov/divisions/marketreg/rule201faq.htm).

These are mechanics, not evidence that any rule makes money.
