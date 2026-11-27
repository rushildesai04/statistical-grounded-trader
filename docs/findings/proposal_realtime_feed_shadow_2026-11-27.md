# Proposal: a real-time full-tape live feed, evaluated in shadow first (2026-11-27)

**Status: PROPOSAL for the paid stage. Nothing is bought, subscribed or switched. The no-cost Stage 0 recorder is
built (section 4), on the owner's authorization of the same day.** Owner 2026-11-27: "For paid data, I authorize
investigation and a concrete integration proposal, not spending or switching feeds yet." and "No purchase, new
subscription, live-feed switch, real orders, or sizing changes without separate authorization." The shadow comparison
in section 5 is written before any of its data exists and fixes what will be measured and what counts as passing.
It measures the feed. It contains no return, win-rate or profit measure, and nothing in it can decide a strategy.

Two statements this document does not make. It does not say a better feed improves returns: that is unknown. It
does not say the feed has no effect on live strategy outcomes because the nightly replay uses full-tape data: the
live book decides WHICH alerts become trades, at what reference price, and when a stop is seen, on the live feed,
and the nightly replay only scores the trades that resulted (section 2).

## 1. What was verified, and where

Read on 2026-11-27 from the vendor's own documentation. "Not stated" means the official pages read do not say it;
those items are open and are the owner's to confirm with the vendor in writing before paying (section 8).

| Item | Verified | Source |
|---|---|---|
| Plans | two: Basic and Algo Trader Plus | [About Market Data API](https://docs.alpaca.markets/docs/about-market-data-api) |
| Monthly cost | Basic free; **Algo Trader Plus $99 / month** | same page, pricing row |
| Real-time coverage | Basic: the IEX exchange only. Algo Trader Plus: all US stock exchanges | same page |
| Historical limit on Basic | the full tape is refused for the latest 15 minutes: "the `end` parameter must be at least 15 minutes old to query SIP data without a subscription" | [Market Data FAQ](https://docs.alpaca.markets/docs/market-data-faq) |
| "Latest" endpoints on the full tape | need the subscription: "All the latest endpoints (including the snapshot endpoint), require a subscription to be used with the SIP feed" | same page |
| Request limit | Basic 200 / min; Algo Trader Plus 10,000 / min | About Market Data API |
| Streaming symbols | Basic 30; Algo Trader Plus unlimited | same page |
| Streaming channels (stocks) | trades, quotes, minute bars, updated bars, daily bars, trading statuses (with halt reason), limit-up / limit-down bands, trade corrections and cancels | [Real-time Stock Data](https://docs.alpaca.markets/docs/real-time-stock-pricing-data) |
| Minute bars | "emitted right after each minute mark"; an "updated bar" follows after the half-minute mark when a late trade arrived | same page |
| A minute with no trade | has NO bar on any feed: a bar is emitted only when none of open, high, low, close, volume is 0 | Market Data FAQ |
| Streaming connections | "in many subscriptions (or without one) this limit is 1"; exceeding it returns code 406 "connection limit exceeded" | [Streaming Market Data](https://docs.alpaca.markets/docs/streaming-market-data) |
| Paper-only accounts | "As an Alpaca Paper Only Account holder, you are only entitled to receive and make use of IEX market data" (as returned by a search of the vendor's documentation; the page was not read in full) | [Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading) |
| Paper keys under a subscribed owner | a staff reply on the vendor's forum says all of an owner's keys, paper or live, get the same data. A forum reply, about a year old, not documentation: treated as unconfirmed | [forum thread](https://forum.alpaca.markets/t/key-limitations-on-paper-trading-keys/17789) |
| Cancelling | terms: charges are made in advance, monthly or yearly, until cancelled; no refund is owed for the unused part of a paid term. As returned by a search; the document uses older plan names: treated as indicative | [Terms and Conditions](https://files.alpaca.markets/disclosures/library/TermsAndConditions.pdf) |

**Not stated on the pages read (open):** whether a paper-only login can buy the plan at all, and whether the paper
keys the runner uses carry the entitlement; any professional / non-professional declaration or exchange subscriber
agreement, and any price difference by status; any restriction on running from a cloud server or on showing the
data to anyone but the subscriber; the exact number of streaming connections on this plan; proration; whether the
quote channel is the national best bid and offer (the pages describe bid and ask fields and do not use that term);
which trade conditions are left out of bars; depth beyond the best bid and offer (none is described).

Two consequences that matter for the design:

1. **A missing candle does not disappear on the full tape.** A minute with no trade has no bar on the paid feed
   either. What changes is what a missing bar can mean, and only if completeness is verified: section 5, measure M2.
2. **The paper-key entitlement is the gating unknown.** The runner holds paper keys only. If the plan cannot be
   bought on a paper-only login, or does not reach paper keys, the cheapest path is closed and the comparison in
   section 3 changes.

## 2. What the live book reads today, and why the feed can change live outcomes

Every live decision reads the free single-exchange feed; the official record is built at night from the full tape.
At commit `e4dd9dc`:

| Live read (`scripts/fade_live.py`) | Line | Used for |
|---|---|---|
| `iex_quote` latest trade, and trades in a window | 157, 193 | the short's live price reads and its stale-print / halt guard |
| `iex_last` latest trade | 92 | the long's between-candle stop look |
| `iex_book` latest quote | 121 | what one exchange was offering when a long was booked (recorded, decides nothing) |
| `iex_context` snapshot | 265 | context on the alert |
| `live_bars` one-minute bars | 330 | every long setup, fill and exit, and the coverage rule |
| `scan_bars` | 497 | the scanner (paused) |
| `official_prints`, `official_bars` (full tape, 16 minutes late) | 359, 386 | the official-tape stop check of an open trade, late by construction (`SIP_LAG`) |

The nightly settle, the execution model and every registered calculation read the full tape after the fact and are
not touched by anything here.

Why the live feed can change outcomes, from findings already on file (not from this proposal):

- The long book replayed as the bot: the free feed "shows bases that are not there" (November: 12 of 31;
  `long_bot_replay_2026-11-19.md`, PLAYBOOK section 7). Those are trades the book took that a full-tape
  book would not have seen as setups.
- A stale single-exchange print set a live entry and its stop (FISN 2026-11-09), and a stock traded 981k shares on
  the tape in a minute the live feed showed nothing usable (POAS 2026-11-12) (PLAYBOOK section 5).
- A live stop is seen only when the feed prints through it; the official-tape check runs 16 minutes late.
- Since `long-live-entry-v2`, a long is not opened across a minute with no candle, and on a thin feed a late
  discovery usually has one (`long_over_record_only_2026-11-25.md` section 2b).

So the feed affects which alerts become trades, the reference they are booked at, and when exits are seen. The
direction and size of the effect on results are not known. The tested record of the long side is negative on the
full tape as well (same finding), so a correct feed is not evidence of an edge.

## 3. Options compared

| | Alpaca Algo Trader Plus | Massive Advanced (formerly Polygon.io) | Schwab Trader API streaming | TradingView |
|---|---|---|---|---|
| Real-time full tape | yes (official docs) | reported yes on the Advanced tier only; cheaper tiers 15 minutes late (third-party pages; not verified on the vendor's page) | reported yes for account holders (third-party pages) | not a data feed: alerts by webhook, no way to pull prices or create alerts by program |
| Monthly cost | $99 (verified) | reported $199 (not verified) | reported $0 (not verified) | a paid plan |
| Change to our code | same host, same endpoints, same keys; the `feed` value changes at the call sites in section 2. **Assumption checked against the code: holds for the request layer.** Not yet checked: response differences that matter (trade conditions, quote fields), see M1 | a new client, and a second vendor whose bars must be reconciled against the nightly record's vendor | auth and token code exist in the repo; the login must be renewed in a browser every 7 days (reported), so an unattended runner goes blind when it lapses | does not fit: symbols come from the alert channel during the day |
| Open before choosing | paper-key entitlement | | the weekly login | |

Alpaca is evaluated first, as the owner asked, because the nightly official record already comes from this vendor:
a shadow comparison against it has one fewer variable. If the paper-key entitlement fails, the fallback order is
Schwab streaming (the intended broker) and then Massive; each would need this proposal redone for it.

Massive serves indicator values (moving averages, MACD, RSI), not buy or sell signals; the book computes its own.

## 4. Stage 0: built, validated on delayed data, no spending

Owner 2026-11-27 (later the same day): "You are authorized to build the proposed no-cost Stage 0 recorder and
validate it on available delayed data, provided it is isolated from the baseline, uses existing access, and does not
interfere with production." Built in the same PR as this revision: `sgt.fast.feed_shadow` and
`scripts/feed_shadow.py`.

| | |
|---|---|
| What it records | at each look of the live long book: which one-minute candles its reader returned, when each was first held closed, and the look before that (so the delivery delay is an interval, not a guess) |
| How it stays out of the way | it looks only at frames the book already fetched (no request in session hours); the frame is handed back unchanged; a look is put on a bounded queue without waiting and dropped when full; its own thread and its own database file; off unless `SGT_FEED_SHADOW=1`; switches itself off after five failed writes in a row |
| Nightly job (21:40 ET) | asks the vendor for the FINISHED candles of the looked-at symbols on the free feed and on the full tape (free once 15 minutes old), classifies every watched minute, appends one line to `reports/feed_shadow/daily.jsonl` |
| Not covered, and not planned | the short book's price reader is not tapped. Owner 2026-11-27: "Do not add short-reader instrumentation unless it resolves a specific remaining question." None is named today, so none is added. Also: a candle delivered more than 12 minutes late is not seen; delay resolution is the book's 15 s pass |

**A missing live candle is one of three different things**, and the record now says which:

| Class | Meaning | Could a full-tape live feed have shown it? |
|---|---|---|
| no trade on the tape | neither finished record has a candle: a genuine no-trade minute, as far as those records show | no: nothing happened |
| tape only | the tape traded, the free feed's one exchange did not | **yes** |
| never held | the free feed's finished record has a candle the live reader was never given | not a coverage question: missing data on the free feed itself |

**Validation run (delayed data, 2026-11-25, one liquid and one thin symbol; an illustration of the method, not a
study).** The session's finished free-feed candles were replayed through a real recorder as if the book had looked
every 15 s, then compared with the finished full tape:

| Symbol | Regular-session minutes | Free feed had a candle | No free-feed candle | of which no trade on the tape | of which traded on the tape only |
|---|---|---|---|---|---|
| SPY | 390 | 390 | 0 | 0 | 0 |
| FCUV | 390 | 111 | 279 | 146 | **133** |

On that one thin stock-day, 133 of the 279 minutes with no free-feed candle traded on the tape. That is the size of
what a single-exchange feed cannot see there; it is one symbol on one day and says nothing about how typical it is.
The recorder on the runner will say that for every symbol the book looks at, with real delivery times.

### 4a. The check after the recorder is deployed, fixed before it has run on the runner (2026-11-27)

Owner 2026-11-27: "verify recorder isolation, output integrity, and measured overhead. Avoid promising literally
unchanged timing; report the observed difference against the predefined tolerance."

The long book already writes down its own cadence (`loop_gaps.jsonl`, loop "long book": every 10 minutes the number
of passes, how many started within 1.1 x the intended 15 s, and the longest single pass, to 0.1 s; a late pass gets
its own line). No instrumentation is added for this check. Baseline read on 2026-11-27 before the deploy, regular
hours, all the days the record holds:

| Day | Passes | Started within 1.1 x 15 s | Late passes | Longest pass per 10 minutes: 90th percentile / maximum |
|---|---|---|---|---|
| 2026-11-24 (full session) | 1,521 | 100.0% | 0 | 0.1 s / 0.3 s |
| 2026-11-25 (full session) | 1,482 | 100.0% | 0 | 0.1 s / 0.4 s |
| 2026-11-26 (market closed) | 1,560 | 100.0% | 0 | 0.0 s / 0.2 s |
| 2026-11-27 (early close) | 1,360 | 100.0% | 0 | 0.0 s / 0.1 s |

Compared: the first full session with the recorder on, regular hours, against the two full sessions above.

| Check | Measured from | Tolerance |
|---|---|---|
| Isolation | the recorder's file is `reports/feed_shadow/shadow.db` and nothing else; the operations database gains no table and no row from it; with the switch off the book's reader is the same function object as before (tested) | exact |
| Output integrity | the recorder's health rows: looks offered, written, dropped, write failures; every written candle readable back; the nightly `feedshadow` line present with its class counts adding to the minutes watched | dropped 0, write failures 0, the recorder not switched off by itself, counts add up |
| Cadence | share of passes started within 1.1 x 15 s; late passes | at least 99.5%; 0 late |
| Pass duration | longest pass per 10 minutes, 90th percentile and maximum over the session | not more than 0.1 s (the record's resolution) above the worse baseline session: 0.2 s and 0.5 s |

**What this comparison can and cannot carry.** The baseline is two full sessions. The record resolves 0.1 s and
keeps one figure per 10 minutes (the longest pass), not a distribution: a cost under 0.1 s a pass cannot be seen in
it, and one session inside the tolerance is not evidence of no effect on a busier day. A pass does more work on a
day with more longs watched, so that count is reported beside the figures. The cadence and duration tolerances are
**provisional** for those reasons. They are also **frozen**: written here before the recorder had run on the
runner, and not tightened or loosened after its results are seen. A figure outside them is reported as outside,
with the figure; a figure inside them is reported with the limits above, not as proof.

**Two different failures, two different responses** (owner 2026-11-27: "Distinguish an ordinary
performance-tolerance breach from active interference with trading tasks").

| | Ordinary breach of the tolerance above | Active interference with the books |
|---|---|---|
| What it is | a session's cadence or pass-duration figure outside the table, judged after the session from the book's own record | the recorder demonstrably in the book's way or exhausting a resource while the books run |
| How it is seen | the nightly read of `loop_gaps.jsonl` | by the recorder itself, at once: 3 offers within 10 minutes that each took over 250 ms in the book's thread; 500 looks dropped within 10 minutes (its writer cannot keep up); 5 failed writes in a row; its database over 400 MB; under 500 MB free on its volume (the runner's volume is 5.2 GB with 2.7 GB free on 2026-11-27); its watchdog unable to look. Or by a person: late-pass lines of the long book, a full volume |
| Response | the recorder is switched off at the next guarded deploy window and the cause is looked at before it goes back on | **off at once, with no restart and nothing done to the books**: the recorder switches itself off (two assignments in memory; from then on a look costs one attribute read), or a person creates the file `reports/feed_shadow/OFF` on the runner and its watchdog switches it off within 2 seconds |
| What stays running | everything | everything: the listener, both books, their threads and their open positions are not restarted, paused or touched. Only the recorder's two threads end |
| Coming back | the next guarded deploy | the `OFF` file (written by the recorder with its reason when it switched itself off) keeps it off across restarts until a person removes it; then the next guarded deploy |

The trips in the second column are safety limits, not the performance tolerance: they exist to end interference
within seconds and are deliberately far outside normal operation (the busiest baseline pass took 0.4 s in total).
They are covered by tests: the switch on a running recorder, a restart with the file present, slow offers, a writer
that cannot keep up, each resource budget, and the nightly line saying that the recorder is off and why. What is
NOT covered by any automatic trip: a cost below those limits that still matters. That is what the after-the-session
comparison above is for, with its stated resolution.

The switch has been exercised in tests only. A drill on the runner (create the file outside session hours, time
the switch-off, confirm the listener's process did not restart) would leave the recorder off until the next guarded
deploy; it is run only on the owner's word.

## 5. The shadow comparison (`feed-shadow-sip-v1`), fixed before any data

**What runs in the paid stage.** The Stage 0 recorder, plus a second reader: at each look, off the book's thread
and with its own timeout, the same request on the full tape, and at each decision the full tape's latest trade and
quote. Written to the recorder's own file. No book, text, total or registered figure reads it.

**Isolation rules.** As Stage 0 (bounded queue, own thread, own database, own switch, no wait in the book's
thread), plus: setups, fills and exits are recomputed on the shadow frames with the book's own pure functions
(`live_setups`, `fills`, `already_over`, `coverage`), so "would the book have decided differently" is answered by
the same code and not a copy.

**Length.** 10 full sessions from the first full session after it is switched on (exchange calendar; an early-close
session counts and is labelled). If fewer than 40 traded alerts (both books together) were compared it is extended
one session at a time to at most 20. No measure is read before the last session ends, except the health counters
(M9, M10), which exist to stop a faulty run.

**What the criteria rest on, and which of them are provisional.** Owner 2026-11-27: "Keep the paid-feed criteria
provisional where unsupported. Do not assume a linear cost from the one-minute-delay result to a 20-second delay."
and "Distinguish feed quality from market capacity." Each criterion below carries one of three labels:

- **structural**: follows from how the books work, not from an estimate (a polling book cannot see anything faster
  than its pass: 15 s long, about 10 s short; an entitlement either answers or does not);
- **measured**: rests on a figure already on file, used only for what it measured;
- **provisional**: a working threshold with no measurement behind the number. It is fixed before the paid stage
  from Stage 0's own figures, or stays report-only if Stage 0 cannot support one.

What is on file, and what it does and does not say:

- The same long buy made ONE MINUTE later did 0.46 points worse per trade on the 403 training base-break trades
  (-0.67% against -0.24%; `long_entry_timing_2026-11-20.md`). That is a measurement at one minute. **It says
  nothing about 5, 20 or 30 seconds: no cost is interpolated from it here**, and the first version of this section,
  which spread it evenly over the minute, is withdrawn. What it does support: delay in the entry is not free, so
  candle latency is measured rather than assumed harmless. The 20 s figure of the first draft is withdrawn for the
  same reason it was unsupported, not because 5 s is known to be right.
- The base break is bought at its candle's close only when the break is seen less than 60 s after that close, and
  the short is taken only inside its registered entry minute. A candle or quote that arrives after those windows
  loses the entry outright. That is structural.
- On the tape, stop fills slipped 0.82% and 1.44% on average in the two samples (median about 0.35%;
  `prereg_fade_stop_slippage_2026-11-09`), and the one stop timed end to end (FCUV 2026-11-25) was seen 7.2 s
  after the tape's first print through it. Neither figure gives a cost per second of late detection.

| | Measure | How it is computed | Criterion (proposed; fixed once accepted) |
|---|---|---|---|
| M1 | Entitlement and shape | a full-tape latest-trade, latest-quote and bars request from the runner with the paper keys at every pre-open check; fields present and typed as the parser expects | every probe answers 200 with the expected fields. One refusal for entitlement fails the trial |
| M2 | Candle delivery | seconds from a minute's end to the first look that holds its closed candle, as an interval (the look before proves a lower bound) | **structural:** at least 99.5% held within one pass (15 s) of the minute's end, so a closed candle is acted on at the first pass after it or the one after. **provisional:** a tighter bound (a working figure of 5 s at 95%) is NOT a pass/fail line until Stage 0 shows what the free feed delivers and how the entry windows above are actually being missed; until then it is reported |
| M3 | Completeness: missing data against no-trade minutes | each watched minute with no live full-tape candle, classified from the next day's finished candles AND the tape's own trade prints for that minute: (a) no print, no finished candle: verified no-trade; (b) prints that form no candle under the vendor's rules: listed with their trade conditions; (c) a finished candle or eligible prints exist: missing data | class (c) at most 0.5% of watched traded minutes, every case listed. Only if this passes can a missing full-tape candle be treated as resolved by the coverage rule, and that remains the owner's decision |
| M4 | Discrepancies between records | every minute where the live full-tape candle, the next-day finished candle, and the tape's prints disagree (existence or range). Each is adjudicated from the prints with their conditions, and counted as: live wrong, finished record wrong, or rule difference (late print, correction, condition excluded from candles) | report with the adjudication. The nightly replay is NOT assumed correct: a "finished record wrong" count above zero is reported against the registered replay's inputs, separately, and corrects nothing by itself |
| M5 | Setup agreement | for each alert: the setup, fill minute and exit the book's functions give on each feed's frame at the same look, and each against the nightly replay; disagreements traced to their minute and adjudicated as in M4 | the full-tape frame agrees with the adjudicated record on setup kind, fill minute and exit reason in at least 95% of compared alerts, AND in more of them than the free-feed frame does (paired, same alerts). If not, a switch is not supported by this trial |
| M6 | Stop-detection delay | for each open paper trade whose stop the tape traded through: seconds from the tape's FIRST PRINT at or through the stop (trade prints, not candles) to the first look whose data showed it, per feed | **structural:** on the full tape, at least 95% within one pass plus 2 s (17 s long book, 12 s short book): a book that polls cannot do better, so anything worse is a feed or transport fault. **provisional:** "none later than 60 s". The free feed's figures are reported beside it. No cost per second of late detection is claimed |
| M7 | Quote freshness and stale data | at each entry decision: age of the latest full-tape trade and quote, and whether both sides are present. A decision is "stale" when the quote is older than 2 s although the tape printed in the last 10 s, or when no trade printed in the last 90 s without a halt status | **provisional:** the 2 s, 10 s and 99% figures are working definitions of "stale" with no measurement behind them; they are fixed from the trial's own first sessions before the rest is read, or the measure stays a report. **structural:** every decision is labelled fresh or stale by a stated rule, every stale case is listed with what the trading-status channel said, and a switch would refuse an entry on stale data instead of guessing |
| M8 | Market capacity at the decision (NOT a feed criterion) | at each entry decision: the displayed size on the side the order would take, against the order's shares at today's paper size and at full size; the spread | **no pass/fail for the feed.** Too little displayed size is a fact about the market at that moment: it can invalidate an ORDER (that size could not have been taken at that quote) and it never makes the feed defective. Reported per decision as: quote present and fresh (a feed property, judged under M7) and, separately, size against the order (a capacity property, an input to the size plan and to the execution model). Depth beyond the best bid and offer is not offered on the pages read and is reported as absent |
| M9 | Failures and recovery | requests by outcome (ok, timeout, 429, 5xx, 403) per feed; longest run of failed looks; looks dropped by the queue. One forced test before the trial: an invalid feed value must raise the alarm of section 7 within two passes and must not fall back | no run of failed looks longer than two passes without the alarm having fired; the forced test passes |
| M10 | Resource use | requests per minute at the busiest minute against the plan's limit; the book's pass duration with the second reader on against the sessions before; operations-database lock waits | peak requests under 25% of the plan's limit; the book's pass duration at the 95th percentile not more than 10% above the sessions before; no new lock wait attributed to the recorder |

**What a passed trial establishes, and what it leaves unproven.**

| Established | Not established |
|---|---|
| the paper keys reach the full tape from the runner, every session | that any book has positive expectancy on it |
| how fast and how completely the full tape's candles arrive, and by which transport | how results change after a switch (that needs the switch, its own versioned record, and time) |
| how often the free feed showed the book a setup, a fill or a stop that the adjudicated record does not have, and the reverse | that the differences help rather than hurt |
| whether a missing full-tape candle can be told from a no-trade minute | what a real order would have filled at (no feed shows a fill) |
| whether displayed size covers the orders the books send | borrow, locate cost, or any constraint outside market data |

**The purchase decision, stated before any spending.** Total cost of the trial as designed: one month, $99. The
cancel-or-continue point is the trial report: any failed STRUCTURAL criterion among M1, M2, M3, M5, M6 means cancel before the
second charge unless a specific, cheap remedy is named in the report. Provisional thresholds decide nothing until
they have been fixed as described above; M8 never decides the purchase. A pass is a reason to consider a switch, not
to make one. Today the economic case for paying is weak, and section 4 of `economic_assessment_2026-11-27.md` says
why: neither alert book has a demonstrated executable edge, and a feed cannot supply one. The recommendation there
is to run Stage 0 (free), resolve the short book's borrow question first, and buy the month only if a book is still
a candidate afterwards.

## 6. What a later switch would change, and what it must not

A switch is a separate authorization after the trial. It would be a versioned change to the LIVE PAPER path only.

| | Under a switch |
|---|---|
| Registered replay, nightly settle, execution model, their ledgers | unchanged: they already read the full tape after the fact |
| Registered cohort (trades from 2026-11-23), its criteria and its primary calculation | unchanged; one cohort across engineering versions, not restarted, nothing excluded |
| Provenance | every alert-book row made after the switch carries the live feed and its version (`live_feed = sip-v1`; rows before it are read as `iex-v0` and are not rewritten). Reports that split by feed version are diagnostic only |
| Historical records | untouched. No row is recomputed on the new feed |
| Live paper eligibility | changes, and each change is named in the switch's own document before it is made: which setups form (the frames differ), the short's stale-print and halt guard (its 90 s rule was set on single-exchange prints), the long's coverage rule (only if M3 passed may a missing full-tape minute be treated as resolved, and that is the owner's decision, not automatic), the between-candle stop look, the booking-time quote |
| Rules whose constants were tuned on the free feed | listed and re-decided one by one in that document; none is carried over silently and none is changed silently |
| Monitoring | the pre-open check proves the entitlement each session (M1's probe); the nightly report states the live feed in one line; the watchdog treats "feed unavailable" as its own alarm, separate from "listener down" |
| Official-tape stop check (16 minutes late today) | would no longer need to wait; that is a behaviour change to exits and is named in the switch document, not assumed |

## 7. Rollback, and no silent fallback to the free feed

- **One setting.** The live feed is one value read at start (`iex` or `sip`), printed in the start-up log, written
  on every row, and stated in the nightly report. The scattered literals of section 2 are replaced by that value in
  the switch PR (not before).
- **Entries fail closed.** If the configured feed is `sip` and a request is refused or fails, the book does not ask
  the free feed instead. The look is recorded as feed unavailable, no new paper trade is opened on it, and the
  owner gets one alarm text (rate-limited). An entry on a silently substituted feed is the failure this rule exists
  to prevent.
- **Open paper positions when the feed fails (owner 2026-11-27: "explicitly mark the position unmonitored and its
  outcome uncertain. No silent fallback or claim of protection.").** From the first failed pass the row is marked
  UNMONITORED with the time it began; no exit check runs and none is simulated; the owner is texted once. No other
  feed is read in its place. When the feed answers again the checks resume and the row records how long it was
  blind. For any trade that was unmonitored at any point, the live estimate is labelled OUTCOME UNCERTAIN: it is
  not a stop, not a result, and is shown as such in the texts and the nightly report. The registered replay scores
  the trade at night from the official record, as it does every trade; that is a measurement after the fact and
  protects nothing.

  Two alternatives are recorded so the choice is visible, not recommended: reading the free feed for a blind
  position (mixes feeds inside one trade and is a fallback, which the owner has ruled out as an automatic
  behaviour), and closing the position when blind (books a price nobody could have traded at).

  Nothing here is protection. A stop in these books is a trigger evaluated by the runner; when the runner is blind
  the trigger does not exist. A resting stop order at a broker is also a trigger, filled at whatever the market
  gives, and is a real-money design question outside this proposal.
- **Rollback.** Set the value back to `iex` and restart inside a deploy window with no alert trade open. Rows made
  after the rollback carry `iex-v0` again. Nothing is deleted. The subscription can be cancelled independently;
  the terms read say no refund is owed for the rest of a paid month.
- **A test in the gate** fails if any live read names a feed literal instead of the setting.

## 8. Owner setup steps, in order (nothing here is done by the bot)

| # | Step | Costs money |
|---|---|---|
| 1 | In the Alpaca dashboard, see whether the login is paper-only or has a live brokerage account | no |
| 2 | Ask Alpaca support in writing (answers are kept with this document): (a) can this login buy Algo Trader Plus; (b) do the PAPER API keys already on the runner get the full-tape entitlement; (c) is a professional / non-professional declaration or an exchange agreement required, and does it change the price; (d) may the data be used by a program on a cloud server, and shown on a private page only the subscriber opens; (e) how many streaming connections; (f) billing date, proration, how to cancel | no |
| 3 | Accept or change the criteria of section 5 | no |
| 4 | Nothing to do: Stage 0 is built and runs on free data from its deploy. Its nightly line is read for at least 5 sessions before step 5 is considered | no |
| 5 | Only then, and only on your word: Plans and Features, Market Data, upgrade to Algo Trader Plus | **$99 / month** from that day |
| 6 | Tell me the date. The next pre-open check runs the M1 probe from the runner. No key is changed or pasted anywhere | no |
| 7 | Stage 1 runs for 10 to 20 sessions; then one report, measures against criteria | within the paid month if started promptly (about 21 sessions a month) |
| 8 | Decide: switch (its own document and PR), extend, or cancel | cancel stops later charges |

Minimum spend to complete the trial as designed: one month, $99. Standing rule unchanged: no new spending without
the owner's word.

## 9. What this proposal does not authorize

No purchase, subscription, feed switch, real order, size change, new entry or exit rule, or change to any
registered calculation, cohort, frozen review or the H1 seal. Fade size stays 25%. Both alert books stay paper.
