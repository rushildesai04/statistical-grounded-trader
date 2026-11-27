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
| Not covered yet | the short book's price reader is not tapped (its registered entry path is left untouched in this version); a candle delivered more than 12 minutes late is not seen; delay resolution is the book's 15 s pass |

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

**Why each criterion is where it is.** The numbers the criteria rest on are the book's own:

- The books look every 15 s (long) and about every 10 s (short). A polling design cannot see anything faster than
  its pass, so the pass is the floor for every "seen within" criterion.
- For a long, the same buy one minute later made 0.46 points less per trade on the 403 training base-break trades
  (`long_entry_timing_2026-11-20.md`). Spread evenly over the minute (an assumption: the comparison was made at one
  minute only), 20 s of extra delay is about 0.15 points a trade and 5 s about 0.04. The tested base break is
  -0.24% a trade at the close against -0.67% a minute later, so 20 s is a material share of the whole difference: **20 s is not adequate for the entry rule, and the
  first draft's 20 s is withdrawn.**
- The base break is bought at its candle's close only when the break is seen less than 60 s after that close; the
  short is taken only inside its registered entry minute. Both are lost, not degraded, by a late candle or quote.
- A stop is a trigger, not a cap: what is lost beyond it grows with every second it is not seen. On the tape, stop
  fills slipped 0.82% and 1.44% on average in the two samples (median about 0.35%;
  `prereg_fade_stop_slippage_2026-11-09`). The one stop timed end to end (FCUV 2026-11-25) was detected 7.2 s after
  the tape's first print through it.

| | Measure | How it is computed | Criterion (proposed; fixed once accepted) |
|---|---|---|---|
| M1 | Entitlement and shape | a full-tape latest-trade, latest-quote and bars request from the runner with the paper keys at every pre-open check; fields present and typed as the parser expects | every probe answers 200 with the expected fields. One refusal for entitlement fails the trial |
| M2 | Candle delivery | seconds from a minute's end to the first look that holds its closed candle, as an interval (the look before proves a lower bound) | at least 95% held within 5 s of the minute's end and at least 99.5% within 15 s (one pass). If polling cannot meet 5 s, the same measure is taken on the vendor's minute-bar stream for timing only, and the result says which transport the entry rule needs |
| M3 | Completeness: missing data against no-trade minutes | each watched minute with no live full-tape candle, classified from the next day's finished candles AND the tape's own trade prints for that minute: (a) no print, no finished candle: verified no-trade; (b) prints that form no candle under the vendor's rules: listed with their trade conditions; (c) a finished candle or eligible prints exist: missing data | class (c) at most 0.5% of watched traded minutes, every case listed. Only if this passes can a missing full-tape candle be treated as resolved by the coverage rule, and that remains the owner's decision |
| M4 | Discrepancies between records | every minute where the live full-tape candle, the next-day finished candle, and the tape's prints disagree (existence or range). Each is adjudicated from the prints with their conditions, and counted as: live wrong, finished record wrong, or rule difference (late print, correction, condition excluded from candles) | report with the adjudication. The nightly replay is NOT assumed correct: a "finished record wrong" count above zero is reported against the registered replay's inputs, separately, and corrects nothing by itself |
| M5 | Setup agreement | for each alert: the setup, fill minute and exit the book's functions give on each feed's frame at the same look, and each against the nightly replay; disagreements traced to their minute and adjudicated as in M4 | the full-tape frame agrees with the adjudicated record on setup kind, fill minute and exit reason in at least 95% of compared alerts, AND in more of them than the free-feed frame does (paired, same alerts). If not, a switch is not supported by this trial |
| M6 | Stop-detection delay | for each open paper trade whose stop the tape traded through: seconds from the tape's FIRST PRINT at or through the stop (trade prints, not candles) to the first look whose data showed it, per feed | on the full tape: at least 95% within one pass plus 2 s (17 s long book, 12 s short book), none later than 60 s. The free feed's figures are reported beside it |
| M7 | Quote freshness and stale data | at each entry decision: age of the latest full-tape trade and quote, and whether both sides are present. A decision is "stale" when the quote is older than 2 s although the tape printed in the last 10 s, or when no trade printed in the last 90 s without a halt status | at least 99% of entry decisions on symbols that printed in the last 10 s have a two-sided quote no older than 2 s. Every stale case is listed with what the trading-status channel said (halt or not). A switch would refuse an entry on stale data instead of guessing; this measure says how often |
| M8 | Executable size | at each entry decision: the displayed size on the side the order would take, against the order's shares at today's paper size and at full size; the spread | at today's size the displayed size covers the order in at least 90% of decisions. At full size it is reported, and a result under 50% is a finding about the size plan, not about the feed. Depth beyond the best bid and offer is not offered on the pages read and is reported as absent |
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
cancel-or-continue point is the trial report: any failed criterion among M1, M2, M3, M5, M6 means cancel before the
second charge unless a specific, cheap remedy is named in the report. A pass is a reason to consider a switch, not
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
- **Open positions when the feed fails: no automatic fallback. Three designs, their risks, and a recommendation.**

  | Design | What happens to an open paper position | Risk |
  |---|---|---|
  | A. Blind hold, alarmed | no exit check runs until the configured feed answers again; the row is marked unmonitored from the first failed pass; the owner is texted once; the time exit still happens by the clock and is priced from the official record at night | a stop is not seen while blind. On paper the official replay still scores the trade, so the record is intact and the live estimate is simply absent. With real money this design is acceptable only if a stop order already rests at the broker |
  | B. Degraded watch on the free feed, by choice | after K failed passes the exit check reads the free feed; the row carries the feed used for every check from then on; alarm | exits decided on the feed the switch was made to leave; the mixed-feed trade must be flagged in every report that splits by feed version. It hides nothing but it does mix |
  | C. Flatten when blind | after K failed passes the position is closed at the last known price | books a price nobody could have traded at; in real trading it is a market order sent without a quote |

  Recommended: **A for the paper books, B only if the owner chooses it explicitly in the switch document, C never.**
  And one requirement that outlives this proposal: no real-money version of either book may depend on the runner's
  own feed for its stop. The stop rests at the broker from the moment of entry, so a feed outage degrades
  monitoring, not protection. The switch document will ask the owner to choose between A and B.
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
