# Two paper losses of 2026-11-25: ALGS short and BNAI long

**Date:** 2026-11-25 (written midday; the nightly settle and ledger come after). Runner v114 = `c12aec4`: both trades
ran on the entry rules of that version. Sources: the decision record, the trade rows, the official tape (trades,
quotes, minute bars, read once they were 15 minutes old), the free feed the book itself reads, the news feed.
Three records are kept apart throughout: the LIVE paper path, the SETTLED rule replay (tonight; not yet available),
and the EXECUTION MODEL on real quotes. None is a broker fill.

## 1. ALGS short (fade id 521)

| Step | Time (ET) | Fact |
|---|---|---|
| Alert posted | 09:57:16 | "volume increasing"; quote 1.97 x 2.00 |
| Received | 09:57:18.8 | 2.8 s after posting |
| Registered entry minute | 09:58:00 | official open 1.905 (first print 09:58:00.568) |
| Checks at the decision | 09:58:08 | company news: none (news feed returned no item for 2026-11-23 to 11-25); beta 1.23; no filing; not alerted in the prior 7 days; IBKR listed 9,000 shares at 8.2% a year at 09:41:58; Alpaca listed it not shortable |
| First pass with no price | 09:58:08.7 | recorded: no free-feed print at or after the entry bar yet (last print 09:57:59) |
| Entry (live paper path) | 09:58:18.8 | 1.93, from a free-feed print of 09:58:12.9; inside the registered minute; official quote then 1.93 x 1.97, 500 shares bid for an order of about 648 |
| Stop level | | 2.316 (1.93 x 1.2) |
| Best price for the short | 10:03:21 | 1.81 on the official tape (not an exit the rule had) |
| First official print at or above the stop | 10:12:48.203 | 2.32 (the minute ran 1.95 to 2.41 on 398,845 shares) |
| First free-feed round lot at or above it | 10:12:48.895 | 2.38 |
| Detected and closed (live) | 10:12:53.003 | 4.8 s after the first official print; estimated exit 2.35 (offer then 2.34 x 100 shares, 2.35 x 800 a second later) |
| Halt | 10:15:01 to 10:20:01 | after the exit; reopened 2.88, high 3.25 at 10:22 |

| Record | Entry | Exit | Result |
|---|---|---|---|
| Live paper path | 1.93 | 2.35, stop | -21.8% before costs; about 1.7 points of estimated costs (spread and the assumed 0.5% locate); -23.5% |
| Settled rule replay | 1.905 at 09:58 (expected) | tonight | not available yet |
| Execution model, 1 s | 1.93 at the bid | 2.37 at the offer one second after the first print through the stop (100 shares shown: thin) | -22.8%, -23.3% with the assumed locate |
| Execution model, 5 s (sensitivity) | 1.93 | 2.34 | -21.7% with the assumed locate |

**The loss in parts (live path):** 20.0 points from the entry to the stop level; 1.8 points beyond it; about 1.7
points of estimated costs. The move through the stop was continuous trading in a fast market: no halt, no gap, and
the free feed showed it within 0.7 s. It is not a missed stop and not mainly slippage.

**What state the stock was in at the entry (from data before it):** prior close 1.49; open 1.60; a slow rise to 1.80
by 09:48 on a few thousand shares a minute; volume then went from 10 thousand to 245 thousand shares a minute between
09:48 and 09:55, the price to 2.12; at 09:56 it fell to 1.70 and came back within the minute; the alert came at
09:57:16 near 1.98. The short went in at 1.93: 9% under a high made three minutes earlier, 29% over the prior close,
nine minutes into the volume expansion. That is the first pullback after a first leg, not a failed breakout (the
high had not been tested again) and not shown to be an exhaustion.

**What supported the fade at that moment:** the registered rule only (an alert of a tested kind, no company news,
beta under 3, borrow listed, price $1 to $50) and its tested average over all such alerts. Nothing specific to this
stock at that minute. The chart reads in the text (MACD rising, EMA9 over EMA21, 1 of 6 reads for the short) are
shown and are not filters: they were tested and carry no edge (section 4).

**News:** our lookup was point-in-time and found nothing; the feed still has no ALGS item hours later, and the channel
posted no news alert. Complete for the sources we read. Whether news existed elsewhere cannot be determined from them.

**Could the short have been made as assumed?** Entry timing: yes, inside the registered minute. Price test: not in
force (low before entry 1.50 against a 1.341 trigger). Spread at the decision: 2.1%. Size: 500 shares bid for 648, so
part of a real order would have filled lower. Borrow: IBKR's list showed 9,000 shares 16 minutes earlier and Alpaca's
showed none; that is each broker's list at its time, not proof either way that the shares could be located.

## 2. BNAI long (long id 84)

| Step | Time (ET) | Fact |
|---|---|---|
| Alerts | 09:09, 09:30:09 | "volume increasing" and a news alert before the open (company news: a deal); "new high" at 09:30:09, received 2.8 s later. No short: alerted in the prior 7 days |
| Halts | 09:30 to 09:35, 09:35 to 09:52 | high 9.42 at 09:35; reopened 6.50 to 7.75 |
| Path before the setup | 09:55 to 10:24 | back to 8.80 at 10:16, then down 10.8% to 7.85 at 10:24 |
| Base, as the book saw it (free feed) | 10:22 to 10:30 | 8 candles, 7.89 to 8.18, 3.68% wide (limit 4%) |
| Break candle (free feed) | 10:31 | closed 8.255: above the base's high 8.18 and above session VWAP 8.174; 4 minutes before the watch window ended |
| The same ten minutes on the official tape | 10:21 to 10:30 | 10 candles, 7.85 to 8.1899, 4.33% wide: NOT a base. No official base break through 10:45 |
| Booked (live paper path) | 10:32:13.6 | 8.255, the break candle's close, 13.6 s after it closed; the quote was then 8.34 x 8.39, last print 8.385 |
| Stop, target, size | | stop 7.89 (the free feed's base low; the tape's was 7.85); target 8.985 (2 x the 4.4% risk); out by 11:02; $303 (the $500 test size x 0.61 for the stop distance) |
| Highest print after the entry | 10:32:16 | 8.41 |
| First official print at or under the stop | 10:38:20.124 | 7.8822 |
| Closed (live) | 10:41:07 | on the free feed's 10:40 candle (it has no 10:39 candle), estimated exit 7.87: 2 min 47 s after the official print |

| Record | Entry | Exit | Result |
|---|---|---|---|
| Live paper path | 8.255 | 7.87, stop | -4.7% before costs; about 0.5 points of estimated costs; -5.1% |
| Settled rule replay | the official 10:32 open, 8.22 (expected) | 7.89, stop (expected) | tonight |
| Execution model, 1 s | 8.39 at the offer | 7.88 at the bid, 10:38:21 | -6.1% |
| Execution model, 5 s (sensitivity) | 8.40 | 7.88 | -6.2% |

**The loss in parts (live path):** 4.4 points from the booked entry to the stop; 0.2 points beyond it; about 0.5
points of estimated costs.

**Was the setup valid?** Valid ON THE FREE FEED, which is what the live rule reads ("a range of at most 4% as the
free feed shows it", playbook section 2): every condition held there on completed candles. NOT valid on full-tape
data: on the official tape it was not a base. The two statements are different and only the first is true of this
trade. That gap is known and registered: `prereg_long_setup_tape_2026-11-22.md` found the feed and the tape agreeing
on 4 of 22 base breaks ("setup validity is NOT established on the free feed, and it is a data-source problem no rule
repairs"). BNAI is one more case, not a new defect. The feed's ranges can only be narrower than the tape's, so it
errs one way: it accepts bases the tape would refuse.

**The 13.6 seconds:** processing time (one pass of the 30-second loop), inside the 90 seconds the rule allows. The
setup had not become invalid: the price was above the base. What had gone stale was the PRICE: the book booked the
candle's close (the rule since 2026-11-20) while the offer was 1.6% higher. On real quotes the entry is 8.39 and the
same stop costs 6.1%, not 4.7%.

**Continuation, rebound or failed breakout?** The rule has no such definition. From data before the entry: the base
sat 12% under the day's high and 6% under the 10:16 high, after a 31% fall through two halts and a partial recovery.
That is a rebound base under a lower high. The price made 8.41 three seconds after the decision and never traded
higher: a break that failed at once.

**Target and hold:** the tested ones (2 x risk, 30 minutes). The target, 8.985, was above the last hour's high of
8.80: the trade needed a new high for the hour to win.

**The exit:** the live book closes a long on the free feed's completed candles, so it saw the stop 2 min 47 s after
the tape did. Here the price was 7.87 to 7.95 in between and it cost nothing; it is a limit of the data source.

## 3. Classification and action

| Finding | Class | Action |
|---|---|---|
| "Loss capped" on both exit texts, with losses larger than the stop distance | reporting defect | fixed: stop triggered, intended price risk, estimated exit, points beyond the stop, costs; "a trigger, not a cap" |
| Entry texts: past win rate and average with no statement of what they are | reporting defect | fixed: a bar replay of every alert, no borrow check, no fills; averages, not a forecast |
| "(too few to judge)" beside a losing live record | reporting defect | fixed: says which way the record stands and which registered reviews decide |
| The long row did not say which base the book had bought | missing evidence | fixed: the setup as seen is stored with the trade and its decision line |
| Nothing said, per trade, whether the base existed on the tape | missing evidence | fixed: the official candles are read the same night and the row says so |
| ALGS entry eligibility, stop detection, news lookup, borrow gate, price test | handled correctly | none |
| BNAI qualification on completed free-feed candles, watch window, VWAP, stop, target, size | applied as the live rule is written (valid on the free feed only; not valid on the tape) | none |
| The free feed accepts bases the tape refuses | known limit (registered 2026-11-22) | evidence added; an owner question (section 5) |
| The long's booked price can lag the offer (1.6% here) | known, by the 2026-11-20 rule; measured nightly | labelled since: the row and the entry text call it a reference and give its age (`long_stop_detection_2026-11-25.md`, section 5) |
| The long's stop was seen 2 min 47 s after the tape | limit of the data source; a deployment blocker | measured on 15 past stops; proposal and blocker in `long_stop_detection_2026-11-25.md` |

## 4. Enhancement hypotheses: none supported

"None supported" means this and no more: each candidate reviewed here lacked support under the definition it was
tested with, on the population and execution assumptions of that test. It does not mean that no improvement
exists, and it does not disprove definitions that were not tested.

| Idea | What earlier work already says | Verdict |
|---|---|---|
| Shorts: tell an active breakout from an exhaustion with chart reads (MACD, EMA9/21, the six reads) | tested: EMA 9/21, RSI, MACD, PSAR, PVT, VWAP and a learned model of 30+ signals carry about 0 before costs (playbook section 3, item 10). The ALGS text shows them as diagnostics for that reason | not proposed |
| Shorts: where in the move the alert arrives (early, middle, late; at or off the high) | already a registered forward test (`prereg_early_alert_fade_forward_2026-11-18`), sealed until 300 alerts. A second test on the same population would be the same question asked twice | not proposed; wait for it |
| Shorts: wait for confirmation before entering | tested: entering 2 and 4 minutes later is worse (-1.65%, -2.64% a trade forward; training +0.14% and -0.21% against +0.97% at the alert) | not proposed |
| Longs: require the base on the official tape | cannot be a live rule: the tape is 15 minutes behind. The effect is known (bases not on the tape: 12 of 31 in November, -2.8%) | not a rule; now measured per trade |
| Longs: only breaks that are continuations (above the day's high) | tested: the "new high" case is worse (-1.94% / -2.62% a trade) | not proposed |
| Longs: base length, width, target, hold | tested 2026-11-19: none is positive on the tape | not proposed |

Evidence against inventing anything from these two trades: each lost for a reason its book's tested record already
contains (25.4% of fades stop out: 194 of 764, `prereg_fade_stop_slippage_2026-11-09`; 63% of base breaks lose). Neither shows a mechanism that the earlier tests missed.

## 5. Owner question

The long book trades setups whose validity cannot be established on the data it reads in time. Two of your standing
decisions meet here:

- 2026-11-18: longs are on, at test size; do not switch them to measure-only.
- 2026-11-25, for shorts: when eligibility cannot be established with timely data, label it unknown and do not enter.

| Option | Effect |
|---|---|
| A. Keep the long rule as defined ("as the free feed shows it"), at test size, with the new per-trade evidence | no change in trading; within a few weeks the nightly stamp shows how the feed-only bases do against the confirmed ones |
| B. Apply the shorts' principle to longs | no base-break long until a timely official feed exists; this is measure-only in effect |

Recommended: A. It is what the rule says and what you decided on 11-18, and the evidence to revisit it is now
collected automatically. Nothing changes until you say otherwise.

> **Corrected 2026-11-25 (afternoon).** Section 4 said "16% of fades stop out". That was the constant on the short
> entry card (`fade_rule.STOP_RATE`), not the registered count, which is 25.4%. The sentence is corrected in place;
> nothing else in this audit used the figure. See `edge_assessment_2026-11-25.md`, section 4, D0.
