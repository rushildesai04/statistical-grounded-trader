# Executable paper baseline: the record re-priced at real quotes (audit phase 2, 2026-11-21)

Owner, 2026-11-21: "Prioritize establishing a trustworthy, executable paper baseline before further strategy
optimization ... Passing tests establishes software behavior, not trading edge. The goal of this phase is trustworthy
evidence, even if that evidence says not to trade." Real money stays locked.

Status: the execution model was frozen in its own commit before any result (`exec_model_2026-11-21.md`), then run once
on every settled alert trade and on the fade's forward record (1,342 read-only requests to the official tape). One
amendment after the results (flags and labels only; recorded in the spec). Results: `reports/exec_model/`. The original
ledger in the ops database is unchanged. Nothing here is a broker fill.

## 0. What the evidence says

| Book | Original record | Re-priced at real quotes | Reading |
|---|---|---|---|
| Alert shorts, the 29 taken | -1.7% a trade | all 29 priced: -1.7%. No exclusion flag (15): **-8.9%** (33% won). Flagged (14): +5.9% | fills were not the problem. The result differs sharply between unflagged and flagged shorts |
| Alert shorts, forward record (77 alerts with a receipt time; 25 of them are the taken shorts above) | +1.3% | all priced: +0.9%. No exclusion flag (37): **-5.2%**. Flagged (36): +6.2% | the same pattern; NOT an independent confirmation (section 2.4 gives the non-overlapping part) |
| Alert longs, the 24 taken | -2.5% | 18 priced: **-3.6%**; 6 would not have been bought at all (the price was already at or under the stop when the bot booked) | loses under every pricing |

**"No exclusion flag" is not "placeable".** It means only: IBKR's list showed enough shares when the alert was
stamped, and the computed short-sale price test was not in force. No broker approved any of these orders, 11 of the 15
unflagged taken shorts had a second broker saying "not shortable", and a flagged short is not proven impossible either
(under the price test a short can still be offered above the bid). Section 2.4 keeps the four things apart: listed
shares, broker approval, price-test status, and whether the ASSUMED order (a market sale at the bid) could be sent.

**What this ledger is, and is not.** The nightly re-pricing from the full tape is a better RESEARCH ledger: it says
what a trade was worth at real quotes. It is NOT stop protection. During a trade the bot still sees under 1% of the
volume and can miss a stop for minutes or entirely (section 3). Real-time, full-tape data and stops resting at a
broker are an unmet requirement for any deployment whose exits depend on the bot seeing prices. Nothing here meets it.

**The registered check of the same evening qualifies the split** (`prereg_fade_price_test_2026-11-21.md`, training
file, run once; outcome NOT SHOWN). On the 1,335 no-news training fades the price-test flag makes no difference: 467
flagged events average +1.37%, 864 unflagged +1.76% (difference -0.4 points, 95% interval -2.5 to +1.7), and the
unflagged average is above zero on its own interval. So the live split is not a feature of the backtest. It stays an
observation on 77 alerts in three weeks, and "the edge was never in shortable stocks" is NOT what the history says.
What the history cannot speak to is borrow (no record of the list before 2026-11-04).

Both alert books stay paper-only. The evidence is small (15 and 37 unflagged shorts, 18 longs; standard errors of 1.6
to 3.1 points): enough to stop calling the short "positive in backtest, unproven live", not enough to call the
unflagged short a proven loser. It shows WHERE the result differs, not WHY: borrow restrictions, the price test,
volatility and the kind of stock overlap, and nothing here separates them. Section 6 fixes what decides each book.

## 1. The short backtest, re-read (item 1)
`scripts/telegram_alert_test.py:70 attach_trades`, `:113 primary_events`; `scripts/alert_fade_test.py:37 cover`, `:59 score`.

| Step | What the backtest does | What a real account faces |
|---|---|---|
| Alert known | 10 s after posting (`REACTION`) | received a median 12 to 40 s after posting by session, 90th percentile 46 to 58 s |
| Entry | the OPEN of the first stored 1-minute bar starting at or after that, within 5 minutes; a trade price | a market sell fills at the bid, after the decision |
| Eligibility | alert type, entry 09:30 to 15:44, price $1 to 50, first alert of the stock that day, not edited later (the edit is only known afterwards: a small look-ahead) | plus: shares to borrow, and the short-sale price test. **Neither is in the backtest.** |
| Stop | first bar whose high reaches entry x 1.2 covers at that level (the bar's open on a gap) | a buy stop fills at the ask after the first trade through |
| Time exit | the close of the last bar inside 60 minutes (may be minutes old in a thin stock; a halt at minute 60 is not modelled) | a market buy at the ask at that moment, or at the reopening |
| Costs | half-spread by price bucket x 2 (0.7 to 1.3%), locate 0.5%, no commission | the spread actually quoted (median 0.7% at entry here), locate unknown |

Replayed with only the information and prices that existed after each decision (section 2): the timing and fill
assumptions cost little on average (0.0 points on the 29 taken shorts; 0.3 points on the 77 forward alerts, standard
error 0.3). The alert delay, which the audit raised, is real (18 of 77 arrive after their official minute opened) but
those alerts average -3.8% in the original and -3.6% re-priced. **The backtest's weak point is not its clock or its
fills. It is that it sells every alert short at a trade price and models neither borrow nor the price test, and the
live result differs sharply across exactly those flags (section 2.4).**

## 2. The reconciled ledger (items 3 and 5)
Labels (spec, section 5): MODELED = priced by the model at 1 s latency and carrying no exclusion flag (NOT a statement
that the order was placeable; section 2.4). UNRESOLVED = priced, but flagged `no_borrow` (IBKR's list showed none or
too few) or `ssr` (short-sale price test in force: the assumed market sale at the bid is not allowed), kept OUT of the
baseline average. "Stops at worst" = STRESS: the stop filled at the
worst price of its minute. Flags never change a price. `late` = the alert arrived after the official entry minute had
opened; `borrow_conflict` = IBKR listed shares, Alpaca said not shortable; `halt` = a fill waited for a trade.

### 2.1 Shorts (29)
| # | Stock | Day | Alert | Original: in → out (why) | Original | Model: in → out (why) | Model | Stops at worst | Flags | Label |
|---|---|---|---|---|---|---|---|---|---|---|
| 7 | CRIS | 11-04 | resumed | 1.515 → 1.1 (60 min) | +25.6% | 1.56 → 1.09 (60 min) | +29.6% |  | no_borrow | UNRESOLVED |
| 11 | CHSN | 11-04 | new high | 4.525 → 4.18 (60 min) | +6.4% | 4.48 → 4.11 (60 min) | +7.8% |  | no_borrow, ssr | UNRESOLVED |
| 12 | PMAX | 11-04 | gaining momentum | 1.02 → 1.055 (60 min) | -4.7% | 1.01 → 1.06 (close) | -5.5% |  | no_borrow, ssr | UNRESOLVED |
| 36 | AMTX | 11-05 | volume increasing | 1.789 → 1.875 (60 min) | -6.5% | 1.79 → 1.88 (60 min) | -5.5% |  |  | MODELED |
| 38 | DNUT | 11-05 | volume increasing | 4.455 → 4.645 (60 min) | -6.0% | 4.44 → 4.65 (60 min) | -5.2% |  |  | MODELED |
| 43 | AEHL | 11-05 | new high | 14.52 → 13.18 (60 min) | +8.3% | 14.52 → 13.22 (60 min) | +8.5% |  | no_borrow | UNRESOLVED |
| 47 | NTCL | 11-05 | resumed | 0.9805 → 1.177 (at live entry) | -21.4% | 1.03 → 1.25 (stop) | -21.9% | -22.8% | borrow_conflict | MODELED |
| 50 | STAK | 11-05 | volume increasing | 1.93 → 1.77 (close) | +7.1% | 1.91 → 1.78 (close) | +6.3% |  | late, borrow_conflict | MODELED |
| 63 | RAYA | 11-06 | news | 1.52 → 1.824 (stop) | -21.8% | 1.52 → 1.824 (stop) | -20.5% | -23.5% | halt, borrow_conflict | MODELED |
| 68 | STAK | 11-06 | volume increasing | 2.17 → 2.24 (60 min) | -5.0% | 2.15 → 2.25 (60 min) | -5.2% |  | borrow_conflict | MODELED |
| 69 | SERV | 11-06 | gaining momentum | 2.88 → 2.935 (60 min) | -3.7% | 2.87 → 2.96 (60 min) | -3.6% |  | late, borrow_conflict, ssr | UNRESOLVED |
| 83 | OMH | 11-09 | new high | 29.2 → 35.04 (at live entry) | -21.2% | 29.2 → 35.43 (stop) | -21.8% | -21.8% | halt, no_borrow, ssr | UNRESOLVED |
| 86 | CKX | 11-09 | new high | 18.93 → 16.06 (60 min) | +13.9% | 18.87 → 16.35 (60 min) | +12.9% |  | late, no_borrow | UNRESOLVED |
| 88 | FISN | 11-09 | volume increasing | 2.43 → 2.77 (stop) | -15.7% | 2.41 → 2.77 (60 min) | -15.4% |  | borrow_conflict | MODELED |
| 90 | NWGL | 11-09 | volume increasing | 4.41 → 5.292 (stop) | -21.8% | 4.32 → 5.26 (stop) | -22.3% | -23.6% | late, no_borrow | UNRESOLVED |
| 98 | TVRD | 11-09 | volume increasing | 1.404 → 1.4 (close) | -1.0% | 1.39 → 1.36 (close) | +1.7% |  | late, borrow_conflict | MODELED |
| 121 | CISS | 11-10 | volume increasing | 2.14 → 1.88 (60 min) | +10.4% | 2.2 → 1.89 (60 min) | +13.6% |  | no_borrow | UNRESOLVED |
| 127 | AMIX | 11-10 | intraday high | 2.32 → 2.784 (stop) | -21.8% | 2.36 → 2.87 (stop) | -22.1% | -27.6% | borrow_conflict | MODELED |
| 139 | STAK | 11-10 | volume increasing | 5.032 → 5.427 (60 min) | -8.8% | 4.95 → 5.43 (60 min) | -10.2% |  | no_borrow | UNRESOLVED |
| 179 | ZDGE | 11-11 | volume increasing | 3.17 → 2.783 (60 min) | +10.5% | 3.15 → 2.8 (60 min) | +10.6% |  |  | MODELED |
| 200 | RETO | 11-11 | volume increasing | 6.34 → 5.915 (60 min) | +5.8% | 6.36 → 5.96 (60 min) | +5.8% |  | borrow_conflict | MODELED |
| 218 | RETO | 11-12 | resumed | 8.33 → 9.996 (stop) | -21.3% | 8.6 → 10.4 (stop) | -21.4% | -22.6% | late, borrow_conflict | MODELED |
| 231 | XLAB | 11-12 | gaining momentum | 1.28 → 1.536 (stop) | -21.2% | 1.28 → 1.54 (stop) | -20.8% | -23.9% | borrow_conflict | MODELED |
| 262 | FCUV | 11-13 | resumed | 6.86 → 5.98 (60 min) | +11.6% | 6.43 → 6.12 (60 min) | +4.3% |  | late, no_borrow | UNRESOLVED |
| 332 | MPU | 11-17 | gaining momentum | 1.6 → 1.92 (60 min) | -21.8% | 1.56 → 1.88 (stop) | -21.0% | -31.9% | late | MODELED |
| 339 | NCPL | 11-17 | gaining momentum | 2.71 → 2.61 (60 min) | +2.5% | 2.69 → 2.63 (60 min) | +1.7% |  | borrow_conflict | MODELED |
| 365 | GMM | 11-18 | intraday high | 2.059 → 1.36 (60 min) | +32.7% | 2.03 → 1.37 (60 min) | +32.0% |  | late, borrow_conflict, ssr | UNRESOLVED |
| 409 | ZJK | 11-20 | resumed | 1.41 → 0.92 (60 min) | +33.0% | 1.36 → 0.93 (60 min) | +31.1% |  | borrow_conflict, ssr | UNRESOLVED |
| 412 | JMIA | 11-20 | volume increasing | 2.785 → 2.575 (60 min) | +6.3% | 2.78 → 2.58 (60 min) | +6.7% |  | ssr | UNRESOLVED |

| | trades | average | standard error | won |
|---|---|---|---|---|
| Original record, all | 29 | -1.71% | 3.08 | 45% |
| Model, all priced | 29 | -1.72% | 3.08 | 48% |
| **MODELED (no exclusion flag; the baseline)** | 15 | **-8.87%** | 3.13 | 33% |
| the same 15 in the original record | 15 | -9.18% | 3.14 | 27% |
| STRESS, 5 s latency | 15 | -8.73% | | |
| STRESS, stops at the worst price of their minute | 15 | -10.51% | | |
| UNRESOLVED (no borrow 10, price test 7, both 3) | 14 | +5.93% | 4.71 | 64% |

Of the 15 MODELED shorts, 11 carry `borrow_conflict` (the second broker said not shortable): the baseline rests on one
broker's list. Only 4 of the 29 taken shorts were listed at IBKR, shortable at Alpaca and free of the price test.
Entry spread: median 0.73%, a tenth above 1.5%. 27 of 29 entries filled at a standing quote; 2 waited for a trade.

### 2.2 Shorts, forward record (every alert with a stored receipt time: 77 of 105; 28 predate the receipt record)
| | alerts | average | standard error | won |
|---|---|---|---|---|
| Original (tested timing, bar prices) | 77 | +1.25% | 2.08 | 51% |
| Model, all priced | 77 | +0.91% | 2.13 | 52% |
| **MODELED** | 37 | **-5.20%** | 2.45 | 46% |
| STRESS, 5 s / stops at worst | 37 | -5.56% / -6.13% | | |
| UNRESOLVED (price test 25, no borrow 20, some both) | 36 | +6.21% | 3.26 | 58% |
| not an event at the fill (price or time out of the rule's band) | 4 | | | |
| alerts received after their official minute opened | 18 | -3.78% original, -3.63% model | | |

The borrow flag exists only where the bot stamped IBKR's list. Of the 48 forward alerts the bot did not take, 36 were
stamped and 12 never were (7 blocked by the repeat rule, 5 by the news rule): those 12 can carry a price-test flag but
never a borrow flag, so the unflagged set contains shorts whose borrow is simply unknown.

### 2.3 Longs (24)
| # | Stock | Day | Setup | Original: in → out (why) | Original | Model: in → out (why) | Model | Stops at worst | Label |
|---|---|---|---|---|---|---|---|---|---|
| 2 | KULR | 11-10 | L4a | 2.167 → 2.13 (stop) | -2.4% | ask 2.01 at or under the stop 2.13 |  |  | NO TRADE |
| 5 | GEVO | 11-11 | L4b | 1.92 → 1.96 (target) | +0.8% | 1.99 → 1.9 (stop) | -4.5% | -5.0% | MODELED |
| 6 | ZDGE | 11-11 | L4a | 3.282 → 3.19 (stop) | -4.1% | ask 2.88 at or under the stop 3.19 |  |  | NO TRADE |
| 7 | NUAI | 11-11 | L6 | 2.375 → 2.425 (target) | +1.4% | 2.38 → 2.44 (target) | +2.5% |  | MODELED |
| 16 | ADXN | 11-12 | L4b | 5.3 → 5.14 (stop) | -3.8% | 5.56 → 6.4 (target) | +15.1% |  | MODELED |
| 17 | POAS | 11-12 | L7a | 4.92 → 4.27 (stop) | -14.5% | 4.8 → 4.25 (stop) | -11.5% | -20.2% | MODELED |
| 18 | RETO | 11-12 | L4b | 8.82 → 9.79 (target) | +10.2% | 9.5 → 8.25 (stop) | -13.2% | -13.2% | MODELED |
| 23 | XLAB | 11-12 | L4b | 1.379 → 1.32 (stop) | -5.0% | 1.38 → 1.32 (stop) | -4.3% | -4.3% | MODELED |
| 25 | SLSN | 11-13 | L4a | 1.275 → 1.24 (stop) | -4.0% | ask 1.24 at or under the stop 1.24 |  |  | NO TRADE |
| 26 | FCUV | 11-13 | L4a | 6.2 → 5.66 (stop) | -9.5% | 6.26 → 5.6 (stop) | -10.5% | -15.7% | MODELED |
| 30 | VTSI | 11-13 | L4a | 3.8 → 3.8 (stop) | -0.7% | ask 3.8 at or under the stop 3.8 |  |  | NO TRADE |
| 37 | GP | 11-17 | L4a | 2.79 → 2.89 (target) | +2.3% | 3 → 2.74 (stop) | -8.7% | -9.3% | MODELED |
| 38 | SPAI | 11-17 | L4a | 3.22 → 3.175 (stop) | -2.6% | 3.2 → 3.16 (stop) | -1.2% | -1.2% | MODELED |
| 39 | OMH | 11-17 | L7a | 6.057 → 5.936 (red) | -2.5% | ask 5.46 at or under the stop 5.69 |  |  | NO TRADE |
| 43 | RAIN | 11-17 | L4b | 1.1 → 1.065 (stop) | -3.9% | 1.15 → 1.05 (stop) | -8.7% | -8.7% | MODELED |
| 44 | NCPL | 11-17 | L4b | 2.708 → 2.68 (stop) | -1.8% | 2.74 → 2.71 (red) | -1.1% |  | MODELED |
| 48 | GMM | 11-18 | L4b | 1.835 → 1.875 (target) | +1.4% | 1.84 → 1.8 (red) | -2.2% |  | MODELED |
| 52 | SKYA | 11-18 | L6 | 3.34 → 3.39 (time) | +0.8% | 3.37 → 3.39 (time) | +0.6% |  | MODELED |
| 54 | POAS | 11-19 | L4a | 3.45 → 3.26 (stop) | -6.8% | ask 3.06 at or under the stop 3.26 |  |  | NO TRADE |
| 55 | GP | 11-19 | L4b | 3.1 → 3.07 (stop) | -1.7% | 3.14 → 3.09 (red) | -1.6% |  | MODELED |
| 56 | MRKR | 11-19 | L4b | 1.31 → 1.303 (red) | -1.3% | 1.31 → 1.35 (target) | +3.1% |  | MODELED |
| 59 | SVRE | 11-20 | L8 | 1.255 → 1.19 (stop) | -6.4% | 1.28 → 1.18 (stop) | -7.8% | -7.8% | MODELED |
| 62 | JMIA | 11-20 | L8 | 2.66 → 2.615 (stop) | -2.4% | 2.66 → 2.61 (stop) | -1.9% | -2.6% | MODELED |
| 65 | LVWR | 11-20 | L8 | 1.01 → 0.9715 (stop) | -4.5% | 1.05 → 0.9506 (stop) | -9.5% | -11.4% | MODELED |

| | trades | average | standard error | won |
|---|---|---|---|---|
| Original record, all | 24 | -2.54% | 0.94 | 25% |
| **MODELED** | 18 | **-3.63%** | 1.59 | 22% |
| the same 18 in the original record | 18 | -2.25% | 1.23 | 33% |
| STRESS, 5 s latency | 18 | -4.26% | | |
| STRESS, stops at the worst price of their minute (the audit's candle-low figure) | 18 | -4.62% | | |
| NO TRADE under the model (original average -3.41%) | 6 | | | |

Trade by trade the two ledgers disagree widely (RETO +10% original, -13% model; ADXN -4%, +15%): all 24 predate the
change of 2026-11-20, so the bot booked each about 72 s after the price it recorded, and a minute is the whole trade
in these stocks. Both ledgers say the book loses. From 2026-11-23 the booking is seconds after the candle and the two
should come closer; the nightly ledger will show whether they do.

### 2.4 Exclusion reasons, kept apart, and how the samples overlap
Four different things. Only the first and the third are observed at all:

| | What we have | What we do not have |
|---|---|---|
| Listed shares (borrow availability) | IBKR's public list, read 6 to 31 minutes before the entry; Alpaca's shortable flag | a locate for the order's size at the order's time |
| Broker approval | nothing | whether any broker would have accepted any of these shorts |
| Short-sale price test | computed from the tape (10% under the prior close that day, or on the prior session) | the exchanges' own list of restricted stocks |
| Feasibility of the ASSUMED order | under the price test a market sale at the bid is not allowed: the assumed order is infeasible | whether a different order (a limit above the bid) would have filled |

By reason (average under the model; standard error in brackets; tiny cells are shown for completeness, not as results):

| | Taken shorts (29) | Forward events priced as events (73; 4 of the 77 fell outside the rule's price or time band at the fill) |
|---|---|---|
| no flag | 15: -8.9% (3.1) | 37: -5.2% (2.4) |
| no listed shares only | 7: +5.2% (6.4) | 13: -1.8% (5.1) |
| price test only | 4: +16.5% (8.9) | 17: +11.7% (4.7) |
| both | 3: -6.5% (8.6) | 6: +7.9% (8.2) |
| any price test | 7: +6.7% (7.4) | 23: +10.7% (4.0) |
| any no-listed-shares | 10: +1.7% (5.2) | 19: +1.2% (4.3) |

On these counts the gap sits with the PRICE TEST more than with missing shares: shorts with no listed shares and no
price test are about flat. That is a description of 7 and 13 trades, not a finding.

Overlap and denominators. The two columns above share trades and must not be read as two confirmations:

| | alerts | no flag | flagged |
|---|---|---|---|
| Taken shorts (priced from the bot's own decision) | 29 | 15: -8.9% | 14: +5.9% |
| ... of which also in the forward set | 25 | 14 | 11 |
| ... of which not in it (AMIX, GMM, NTCL, OMH: no forward event or outside its rule) | 4 | | |
| Forward events (priced from the receipt time) | 73 | 37: -5.2% | 36: +6.2% |
| ... of which are the taken shorts | 25 | 14: -5.5% | 11: +5.8% |
| ... of which the bot did NOT take (the only part independent of the first row) | 48 | 23: -5.0% (3.4) | 25: +6.4% (4.2) |
| Distinct alerts in all | 77 | 38 | 39 |

So there are 77 distinct alerts on 13 sessions, not 29 + 77. The independent evidence is the first row and the
not-taken row: the same direction in both, each inside about two standard errors of zero on its own.

### 2.5 What is verified, modeled, stress, unresolved
- VERIFIED (observed): posting, receipt and decision times; every quote and trade used; the borrow list as stamped.
- MODELED: every fill above (1 s latency, top-of-book price, last-sale stop trigger).
- STRESS: the two labelled columns. Bounds, not estimates.
- UNRESOLVED: 14 shorts and 36 forward alerts (borrow, price test); whether IBKR would have approved any short at
  all; depth beyond the top of the book; halts read from the tape, not from a halt feed; the 28 forward alerts
  without a receipt time; the price test's carry-over read from unadjusted daily bars (a split on the prior day
  would mis-set it).

## 3. Stop-monitoring coverage (item 2)
During the holds the live feed carried a median 0.7% (shorts) and 0.5% (longs) of the official volume.

| | shorts (29) | longs (19 with a live exit) |
|---|---|---|
| official minutes that the live feed also saw (10th / median / 90th percentile) | 37% / 70% / 100% | 64% / 95% / 100% |
| trades with under half the minutes | 6 | 0 |
| longest silence of the feed inside a hold (median / 90th / max) | 6 / 16 / 28 min | 2 / 4 / 11 min |
| the official tape crossed the stop level | 9 | 14 |
| of those, the live feed showed it within a minute | 6 | 9 |
| showed it late | 1 (33 min) | 2 (14 and 21 min) |
| never showed it | 2 | 3 |

Of 23 stop crossings, 15 were seen within a minute, 3 came 14 to 33 minutes late and 5 never came.

What reliable monitoring needs:
1. Coverage: every trade of the consolidated tape, as it prints. One exchange with under 1% of the volume cannot do it.
2. Latency: seconds. The free official tape is 15 minutes old: it corrects a text, it does not protect a position.
3. Failure controls: a stop that RESTS at the broker (it triggers without the bot), a heartbeat on the feed per held
   stock, and a nightly re-pricing of every trade from the tape.

What is done here: the last part of control 3. The nightly ledger (section 5) re-prices every trade from the complete
tape, so the paper RECORD no longer depends on what the live feed saw. No strategy definition was changed.

**Unresolved deployment requirement.** Seeing a stop as it happens is not solved and cannot be on free data. Until
real-time full-tape prices and broker-resting stops exist, (a) every live text is an estimate, (b) a stop can be
crossed without the bot acting, and (c) no book whose exit depends on the bot's own monitoring may be considered for
real money, whatever its paper result. Owner's choice of 2026-11-21 for the meantime: correction texts for longs when
the delayed official tape shows a stop the live feed missed (as shorts already have). That shortens the time a wrong
text stands; it protects nothing.

## 4. The other books: what was inspected, and only that (item 4)
Read-only trace of the daily books by an independent reader; the items marked "confirmed" I re-read myself.

| Book | Decision | Booked fill | What the record assumes |
|---|---|---|---|
| megacap, megacap 15 (15:50 ET) | 15:50:15 from single-exchange 1-minute bars before 15:50, official daily bars to the day before, today's open | the official close, read at 20:50; 5 bp x turnover | an order in the closing auction. NYSE accepts market-on-close orders until 15:50:00, Nasdaq until 15:55: **for NYSE names the close is not an order this decision could send** (confirmed). Measured on all 149 booked orders (100 stock-days, 14 sessions): the close differed from the 15:51 open by a median 22 bp (90th percentile 72, most 148) and from the 15:51-to-16:00 average price by a median 3 bp (90th percentile 15). Signed, trading at 15:51 instead of the close would have cost -5.7 bp an order (standard error 3.7: not against the books). If EVERY gap had gone against a book, its value would be lower by 16 to 21 bp a session (megacap 266 bp over 13 sessions against a signed -49; megacap 15: 124 against +2; long calls: 81 against -12) |
| long calls (`megacap_long`) | the +1 calls of both sets, only when both decided live | as above | its deciding ledger and its paper book are computed differently (4 bp a position-day against 5 bp x turnover; batch days counted in the ledger, never traded in the book): they disagree, measured: the ledger says -4.40% for its 4 sessions (11-16 to 11-19); the paper book says -2.54% over those same dates and -5.42% over the dates shifted by one (a ledger day is a call marked to the NEXT close). The two are 1.0 to 1.9 points apart depending on that alignment, and the registered stop (-5%) is read from the ledger while the book is already past it. NOT reconciled line by line: open item |
| overlay, ETF yardsticks, turn-of-month | calendar or month-end rules | a close | ETF record weights are written after the close they are filled at (reported, not re-read) |
| rebalance | hourly, first 5 sessions | Alpaca PAPER limit orders | the only book with (paper) broker fills |
| Schwab pilot | off unless `SGT_PILOT_SCHWAB=1` | real | cron passes no live switch (confirmed in the entrypoint; Fly secrets not read) |

Checked and found correct IN THE LINES READ: no same-day close enters a model decision (`megacap/daily.py:74,84-85`,
`megacap/live.py:152-154`); the monthly retrain uses rows before the month only (`live.py:104-127`).

Two control gaps, confirmed and fixed as their own pull requests:
- #192: a failed 15-name decision ended the 15:50 job before the steps that close a book with no live decision.
- #193: on a half day (next 2026-11-27) the 15:50 job comes after the close; nothing closed yesterday's calls.

NOT inspected by anyone in this audit: `ops/approvals.py`, `pilot/executor.py`, `pilot/ledger.py`, `pilot/fade_real.py`,
`megacap/score.py`, `megacap/rules.py`, `megacap/downday.py`, `brokers/*`, the bar store, `allocation/signals.py`, the
scanner (paused). No statement in this document covers them.

## 5. What changed in code (the fixes)
| Change | Kind |
|---|---|
| `sgt.fast.exec_model` (new): fills from official quotes and trades from the bot's decision; `scripts/exec_ledger.py`: the reconciled ledger; `sgt.fast.exec_book`: its reader | the executable baseline. Reads only; changes no stored result |
| Nightly job `execledger` (21:25 ET): every newly settled trade appended to `reports/exec_model/ledger.jsonl`; one line in the nightly summary (it replaces the bar-based "execution check" line once the ledger has trades) | measurement |
| #191 unknown beta = no short (owner's decision) | eligibility, fail closed |
| #192, #193 daily-book controls | failure controls; no rule changed |

Not changed: any entry rule, exit rule, size, gate or flag of any book.

## 6. The registered review of each experimental book (item 6)
Fixed on 2026-11-21, before any trade of the sample existed, and coded in `sgt.fast.exec_book.review` so the nightly
line reports it mechanically. The thresholds are the ones first written this day; what follows completes them.

**Sample.** Trades of sessions from 2026-11-23 on (nothing in this audit has seen them), labelled MODELED by the nightly
ledger (priced at 1 s latency, no exclusion flag), in the order they were made. The review reads the FIRST N of them,
never a later window. Flagged, NO TRADE and unpriceable trades are reported beside it and never counted.

> Note added 2026-11-23, no rule changed: "nothing in this audit has seen them" was true on 2026-11-21. From the
> sample's first session its trades are seen as they happen (dashboard, texts) and losses are reviewed; that is
> operational visibility. What stays forbidden is changing a rule, the sample or a criterion on what is seen, and
> acting on any number other than the ones below at their fixed counts.

**Average and lower bound.** The average is the plain mean of the modeled results. Its lower bound is the 10th
percentile of the means of 10,000 resamples of whole SESSIONS (seed 20261123): trades of one day move together, so
days are resampled, not trades. With fewer than two sessions there is no bound and a book cannot be kept.

**Correlated trades.** Two trades on one day are one day's evidence (the bound above). A short and a long on the same
stock belong to different books and are reviewed apart. The forward test of every alert (300 alerts, amendment F1)
overlaps the short book's sample: it is reported with its own split and never added to it as a second confirmation.

**One early look per book**, at a fixed count, reading one fixed number: the average of the first 15 longs (pause if
under -3%) or the first 25 shorts (pause if under -5%). A pause is a question to the owner. The early look can never
keep a book, and no other number is acted on before the review point.

| Book | Review at | RETIRE | KEEP (paper) | Anything else |
|---|---|---|---|---|
| Alert long | the first 30 | average at or under 0 | average above 0 AND lower bound above -1% | INCONCLUSIVE |
| Alert short | the first 50 | average at or under -2% | average above 0 AND lower bound above -2% | INCONCLUSIVE (for example an average of -1%) |

**Inconclusive.** The book remains paper-only and the sample is extended ONCE: longs to the first 60, shorts to the
first 100, or to 2027-02-26, whichever comes first. The same three tests are then applied a last time. A book that is
still neither retired nor kept is NOT ESTABLISHED: the review is over, it is not a candidate for real money, and
whether its paper trading goes on is the owner's choice. A book with fewer than 10 trades by that date cannot be
judged and is NOT ESTABLISHED. There is no second extension.

**What KEEP means.** That the paper book goes on. It is never, by itself, a reason for real money: section 3's
requirement and a broker's own fills come first.

| Other book | Rule |
|---|---|
| Alert short, registered forward test (300 alerts, amendment F1) | its own rule, unchanged; reported with the flagged / unflagged split |
| Long calls (`megacap_long`) | its registered rule: stop at -5% on the ledger (now -4.4%), or 2027-04-30. Its ledger and book must be reconciled first (section 4) |
| megacap, megacap 15 | measurement books; no criteria beyond their registered ones |
| Overlay | owner 2026-11-21: kept as a yardstick. It is a basket for comparison, not a call; its -5% line (tripped 2026-11-19, now -11.0%) is not a stop |
| Scanner | retired (failed its test; paused) |

**No strategy search.** Owner, 2026-11-21: no new search and no change to the frozen rules on these results. One
registered diagnosis was approved the same day and adopted nothing: whether the training backtest's average sits in
stocks under the price test (`prereg_fade_price_test_2026-11-21.md`; outcome NOT SHOWN, section 0). Not started, each needing its
own plan: whether unflagged shorts are profitable (only new trades can say), and whether a short under the price test
could be sold above the bid (needs prints after the order).

## 7. Remaining uncertainties
- Order latency (1 s assumed), queue position, depth beyond the top of the book, and whether a broker would have
  approved each short: none can be known without a broker.
- The price-test flag is computed, not read from an exchange list; a split on the prior day would mis-set it.
- 15 and 37 unflagged shorts, 18 longs: small samples. The split between unflagged and flagged shorts was defined
  before the results, but its size (about 11 to 15 points) is an observation on overlapping samples (77 distinct
  alerts), not a test, and it does not say why: borrow, the price test, volatility and the kind of stock overlap.
- "Unflagged" is the absence of two flags, not a verified ability to short. Broker approval is unknown for every trade.
- Real-time stop protection does not exist (section 3).
- The daily books' close fills are bounded (16 to 21 bp of book value a session at the very worst), not re-priced
  order by order; the long-calls ledger and its paper book are 1.0 to 1.9 points apart and not reconciled.

## 8. Validation of the work itself
- Model frozen before results; one run; two dated amendments (flags, labels and one code strictness), neither changed
  a reported number. The first results are kept as produced next to the amended copy.
- Tests: 2,116 before the audit, 2,189 now; lint clean. The model has 20 tests, the ledger script 11, its reader and
  the registered review 11.
- One independent code review, told to hunt for look-ahead and for anything that would change a reported average:
  graded sound. Its one material point (the price-test flag read the order's own minute) was re-checked on the tape
  for all 106 rows: no flag changed; the code now reads completed minutes only.
- What none of this validates: that any rule makes money, or that a broker would have filled any of these orders.
