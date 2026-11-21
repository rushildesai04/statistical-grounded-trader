# Executable paper baseline: the record re-priced at real quotes (audit phase 2, 2026-11-21)

Owner, 2026-11-21: "Prioritize establishing a trustworthy, executable paper baseline before further strategy
optimization ... Passing tests establishes software behavior, not trading edge. The goal of this phase is trustworthy
evidence, even if that evidence says not to trade." Real money stays locked.

Status: the execution model was frozen in its own commit before any result (`exec_model_2026-11-21.md`), then run once
on every settled alert trade and on the fade's forward record (1,342 read-only requests to the official tape). One
amendment after the results (flags and labels only; recorded in the spec). Results: `reports/exec_model/`. The original
ledger in the ops database is unchanged. Nothing here is a broker fill.

## 0. What the evidence says

| Book | Original record | Re-priced at real quotes (MODELED) | Reading |
|---|---|---|---|
| Alert shorts, the 29 taken | -1.7% a trade | all 29 priced: -1.7%. The 15 a real account could plausibly place: **-8.9%** (33% won). The 14 flagged no-borrow or price-test: +5.9% | fills were not the problem; **availability is**. The winners sit in the shorts flagged as not placeable |
| Alert shorts, forward record (77 alerts with a receipt time) | +1.3% | all priced: +0.9%. Placeable 37: **-5.2%**. Flagged 36: +6.2% | the same split on a wider sample (25 of the 77 are the taken shorts) |
| Alert longs, the 24 taken | -2.5% | 18 priced: **-3.6%**; 6 would not have been bought at all (the price was already at or under the stop when the bot booked) | loses under every pricing |

Not to trade is what this says for both alert books as they stand. The short's evidence is 15 and 37 trades with a
standard error of 2.5 to 3 points: strong enough to stop calling the short "unproven but positive in backtest", not
strong enough to call the placeable short a proven loser. Section 6 fixes what decides it.

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
fills. It is that it shorts every alert, while a real account can only short what it can borrow and what is not under
the price test.**

## 2. The reconciled ledger (items 3 and 5)
Labels (spec, section 5): MODELED = priced by the model at 1 s latency, placeable as far as the flags know. UNRESOLVED =
priced, but flagged `no_borrow` (IBKR's list showed none or too few) or `ssr` (short-sale price test in force: a market
short cannot execute at the bid), kept OUT of the baseline average. "Stops at worst" = STRESS: the stop filled at the
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
| **MODELED (the baseline)** | 15 | **-8.87%** | 3.13 | 33% |
| the same 15 in the original record | 15 | -9.18% | 3.14 | 27% |
| STRESS, 5 s latency | 15 | -8.73% | | |
| STRESS, stops at the worst price of their minute | 15 | -10.51% | | |
| UNRESOLVED (no borrow 10, price test 7, both 3) | 14 | +5.93% | 4.71 | 64% |

Of the 15 MODELED shorts, 11 carry `borrow_conflict`: even the baseline rests on one broker's list.
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

The borrow flag exists only where the bot stamped IBKR's list (alerts it carried to the entry); an alert filtered
earlier has no stamp and counts as placeable. So the MODELED set still contains shorts with unknown borrow.

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

### 2.4 What is verified, modeled, stress, unresolved
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
tape, so the paper RECORD no longer depends on what the live feed saw. Not done, because free data cannot do it:
seeing a stop as it happens. That needs the owner's pending broker market data (real-time, full tape) or a paid tape.
No strategy definition was changed.

## 4. The other books: what was inspected, and only that (item 4)
Read-only trace of the daily books by an independent reader; the items marked "confirmed" I re-read myself.

| Book | Decision | Booked fill | What the record assumes |
|---|---|---|---|
| megacap, megacap 15 (15:50 ET) | 15:50:15 from single-exchange 1-minute bars before 15:50, official daily bars to the day before, today's open | the official close, read at 20:50; 5 bp x turnover | an order in the closing auction. NYSE accepts market-on-close orders until 15:50:00, Nasdaq until 15:55: **for NYSE names the close is not an order this decision could send** (confirmed; size of the error unmeasured, small for large caps) |
| long calls (`megacap_long`) | the +1 calls of both sets, only when both decided live | as above | its deciding ledger and its paper book are computed differently (4 bp a position-day against 5 bp x turnover; batch days counted in the ledger, never traded in the book): they can disagree (reported by the reader, not re-read by me) |
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

## 6. Criteria, fixed now, for each experimental book (item 6)
Judged on the MODELED figure of the nightly ledger, on trades from 2026-11-23 on (data nothing in this audit has seen).
No rule of a book is changed inside its window except a bug fix; a proposed rule change is registered first and judged
only on trades after its registration. Each line ends in a decision put to the owner; nothing switches itself.

| Book | Continue (paper) | Pause for a decision | Retire |
|---|---|---|---|
| Alert long | to 30 new MODELED trades | early, at 15, if their average is under -3% | at 30 if the average is at or under 0. To keep it needs an average above 0 with its 90% lower bound above -1% |
| Alert short | to 50 new MODELED trades (placeable only; UNRESOLVED never counts for it) | at 25 if the average is under -5% | at 50 if the average is at or under -2%; keep only if above 0 with the lower bound above -2%; in between, the owner decides |
| Alert short, registered forward test (300 alerts, amendment F1) | unchanged, but reported with the MODELED / UNRESOLVED split | | its own rule |
| Long calls (`megacap_long`) | its registered rule: stop at -5% on the ledger (now -4.4%), or 2027-04-30 | | its own rule |
| megacap, megacap 15 | measurement books, no criteria beyond their registered ones | | |
| Overlay | its -5% stop tripped on 2026-11-19 (-11.0% now); the stop only alarms | the owner decides now | |
| Scanner | | | retired (failed its test; paused) |

Strategy changes are NOT assessed in this phase. Three questions the evidence raises, each of which needs its own
registered plan before any data is looked at for it:
1. Is the placeable short (shares listed, no price test) profitable? Only new forward trades can answer it.
2. Does the backtest's edge sit in stocks under the price test? The test can be computed for the past from daily bars;
   borrow cannot (no history of the list before 2026-11-04). Training file only; the held-out file stays closed.
3. A short under the price test can still be sold above the bid with a limit order. Whether such an order fills is not
   knowable from quotes at one second; it needs prints after the order and its own model.

## 7. Remaining uncertainties
- Order latency (1 s assumed), queue position, depth beyond the top of the book, and whether a broker would have
  approved each short: none can be known without a broker.
- The price-test flag is computed, not read from an exchange list; a split on the prior day would mis-set it.
- 15 and 37 placeable shorts, 18 longs: small samples. The split between placeable and flagged shorts was defined
  before the results, but its size (about 11 to 15 points) is an observation on overlapping samples, not a test.
- The daily books' close fills and the long-calls ledger were read, not re-priced.
