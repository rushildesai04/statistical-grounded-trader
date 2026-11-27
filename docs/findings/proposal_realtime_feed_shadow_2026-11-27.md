# Proposal: a real-time full-tape live feed, evaluated in shadow first (2026-11-27)

**Status: PROPOSAL. Nothing is bought, subscribed, switched or built.** Owner 2026-11-27: "For paid data, I authorize
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

## 4. Stage 0: build and validate the recorder with no spending

The recorder of section 5 can be built and run before any subscription: the full tape older than 15 minutes is free,
and the runner already reads it. Stage 0 records the live free-feed frame at each look and, 16 minutes later, the
full-tape bars for the same window. It answers "how far is the live feed from the tape" (M2 to M5 for the free
feed's side) and proves the recorder works. It cannot answer how fast or how complete the real-time full tape is;
only Stage 1 (the paid month) can. Paying before Stage 0 runs clean would be paying to debug the recorder.

## 5. The shadow comparison (`feed-shadow-sip-v1`), fixed before any data

**What runs.** Beside the live book and never inside its decision: at each look the book makes for an alert, a
recorder stores what the live feed returned (already fetched) and requests the same thing from the full tape.
The full-tape answer is written to its own file. No book, text, total or registered figure ever reads it.

**Isolation rules.**

- The recorder runs off the book's thread with a bounded queue; when the queue is full a look is dropped and
  counted, never waited for. Each request has its own timeout.
- It writes to a separate SQLite file (`reports/feed_shadow/shadow.db`), not the operations database, so it can
  never hold that database's write lock.
- It has its own off switch (a flag) and is off by default.
- Setups, fills and exits are recomputed on the shadow frames with the book's own pure functions
  (`live_setups`, `fills`, `already_over`, `coverage`), so "would the book have decided differently" is answered
  by the same code, not a copy.

**Length.** 10 full sessions from the first full session after it is switched on (exchange calendar; an
early-close session counts and is labelled). If fewer than 40 traded alerts (both books together) were compared,
it is extended one session at a time to at most 20. No measure is read before the last session ends, except the
health counters of M7 and M8, which exist to stop a faulty run.

**Measures and proposed acceptance criteria.** The criteria are proposals for the owner to accept or change BEFORE
Stage 1 starts; after that they are fixed. Where no defensible threshold exists the measure is report-only and
says so.

| | Measure | How it is computed | Proposed criterion |
|---|---|---|---|
| M1 | Entitlement and shape | a full-tape latest-trade, latest-quote and bars request from the runner with the paper keys at every pre-open check; fields present and typed as the parser expects | every probe answers 200 with the expected fields. One refusal for entitlement fails the trial |
| M2 | Completeness of live bars | each live full-tape minute bar held at a look, against the full-tape bars for that minute fetched the next day: present both times; present next day but not held live; equal open, high, low, close | minutes present the next day and already closed 20 s or more at the look: at least 99.5% were held live. Every miss is listed with its delay. Below that, a missing candle on this feed stays "unknown" and the coverage rule gains nothing |
| M3 | Timestamps | for each minute, seconds from the minute's end to the first look that holds its bar, per feed; clock offset between the runner and the feed's trade times | report-only: median, 95th percentile, maximum, per feed |
| M4 | Missing candles | per look, the coverage classes (held / missing with later data / none after) on each feed's frame; for each full-tape missing minute, whether the next-day record also has no bar | reported side by side. Criterion is on M2, not on "fewer missing": a missing minute counts as verified quiet only when the next-day record agrees |
| M5 | Setup agreement | for each alert: the setup, fill minute and exit the book's functions give on each feed's frame at the same look, and each against the nightly official replay. A three-way table, counts only | the full-tape frame agrees with the nightly replay on setup kind, fill minute and exit reason in at least 95% of compared alerts, AND in more of them than the free-feed frame does (paired, same alerts). If it does not, a switch is not supported by this trial |
| M6 | Stop detection | for each trade whose nightly replay has a stop: the first minute each live feed showed a print at or through the stop, against the replay's stop minute | report-only counts: seen in the same minute / later / not seen within the hold, per feed |
| M7 | Quote freshness, spread, size | at each decision: age of the latest trade and of the latest quote per feed; two-sided or not; spread; displayed size against the order's shares | report-only distributions. Depth beyond the best bid and offer is not offered on the pages read and is reported as absent, not as zero |
| M8 | Failures and recovery | requests by outcome (ok, timeout, 429, 5xx, 403) per feed; longest run of failed looks; looks dropped by the queue. One forced test before the trial: an invalid feed value must raise the alarm of section 7 within two passes and must not fall back | no run of failed looks longer than two passes without the alarm having fired; the forced test passes |
| M9 | Resource use | requests per minute at the busiest minute against the plan's limit; the book's own pass duration with the recorder on against off (the sessions before); operations-database lock waits | peak requests under 25% of the plan's limit; the book's pass duration at the 95th percentile not more than 10% above the sessions before; no new lock wait attributed to the recorder |

Streaming (a websocket) is NOT part of this trial. The book polls closed candles every 15 s; changing how data
arrives and which data arrives at once would leave two causes for every difference. The stated connection limit
(one, in many subscriptions) also makes a stream a single point of failure that needs its own reconnect design.
"Disconnect recovery" is therefore measured for requests (M8). A stream would be a second, separate proposal.

**What the trial cannot show.** Whether results improve. Ten sessions of feed agreement say nothing about
expectancy, and no trade result is collected by the recorder.

## 6. What a later switch would change, and what it must not

A switch is a separate authorization after the trial. It would be a versioned change to the LIVE PAPER path only.

| | Under a switch |
|---|---|
| Registered replay, nightly settle, execution model, their ledgers | unchanged: they already read the full tape after the fact |
| Registered cohort (trades from 2026-11-23), its criteria and its primary calculation | unchanged; one cohort across engineering versions, not restarted, nothing excluded |
| Provenance | every alert-book row made after the switch carries the live feed and its version (`live_feed = sip-v1`; rows before it are read as `iex-v0` and are not rewritten). Reports that split by feed version are diagnostic only |
| Historical records | untouched. No row is recomputed on the new feed |
| Live paper eligibility | changes, and each change is named in the switch's own document before it is made: which setups form (the frames differ), the short's stale-print and halt guard (its 90 s rule was set on single-exchange prints), the long's coverage rule (only if M2 passed may a missing full-tape minute be treated as resolved, and that is the owner's decision, not automatic), the between-candle stop look, the booking-time quote |
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
- **Open positions are the one stated exception, and it is loud.** A paper position that is already open keeps
  being watched: if the configured feed is unavailable, its exit check may read the free feed, the row is marked
  degraded with the feed that was used, and the alarm says so. An unwatched open position is the worse outcome.
  The owner can choose "no exception" instead; the switch document will ask.
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
| 4 | Say "build Stage 0": the recorder is built, reviewed, merged and run on free data until it is clean | no |
| 5 | Only then, and only on your word: Plans and Features, Market Data, upgrade to Algo Trader Plus | **$99 / month** from that day |
| 6 | Tell me the date. The next pre-open check runs the M1 probe from the runner. No key is changed or pasted anywhere | no |
| 7 | Stage 1 runs for 10 to 20 sessions; then one report, measures against criteria | within the paid month if started promptly (about 21 sessions a month) |
| 8 | Decide: switch (its own document and PR), extend, or cancel | cancel stops later charges |

Minimum spend to complete the trial as designed: one month, $99. Standing rule unchanged: no new spending without
the owner's word.

## 9. What this proposal does not authorize

No purchase, subscription, feed switch, real order, size change, new entry or exit rule, or change to any
registered calculation, cohort, frozen review or the H1 seal. Fade size stays 25%. Both alert books stay paper.
