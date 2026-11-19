# SGT Playbook — the alert fade (living knowledge base)

Single source of truth for what the fade does, why, what we have learned, and how it gets smarter. Updated after every
study and every loss review (owner, 2026-11-06: "distill all of it ... keep making it more intelligent"). Numbers cite
the file that produced them; "train" = 2,349 fades 2025-01..2026-06, "held-out" = 764 fades 2026-07..10 never used to
build the rule.

## 0. Standing rules (owner)
**Longs first; shorting is deprioritized (owner 2026-11-12 night).** A short sells BORROWED shares: the broker owns them,
so every cent the stock rises above the entry is the short seller's loss with no ceiling (XLAB 11-12 went +95% after
the alert; a long can lose at most what was paid). Shorting needs a margin account and collateral beyond the trade
(Reg T: 150% of the short's value at entry; FINRA 4210 maintenance for shorts under $5: $2.50/share or 100% of
value, whichever is greater [VERIFY at the chosen broker]), pays a borrow fee on hard-to-borrow names (XLAB 482%/yr),
can be force-covered when the lender recalls the shares (buy-in), and many brokers refuse small-cap shorts outright.
Our paper fade charged only costs + a 0.5% locate and assumed shares were always there. So: no short rule goes live
or back on without (a) real borrow availability and fees inside its test, (b) a hard loss cap per trade, and (c) the
owner's explicit OK. New strategy work goes to the long side.

**Both alert books back ON, paper (owner 2026-11-16, 12:10 ET).** With the fade off (11-12) and the alert longs
measure-only (11-13) the runner received 26 alerts that morning and sent no text; the owner read the silent phone as a
failure. Shown the options and both books' numbers (fade: November forward -3.44% per trade, win 39%, n 61; longs:
-2.09% per trade in training), the owner chose: "turn them back on ... we need to see how they perform and test, thats
the whole point of this. i should be receiving all alerts and texts to my phone". `fade_off=0`, `long_off=0` since
12:13:57 ET (audit event `flag`); the drift guard still halves the fade's size. For the fade this is (c) only: (a) borrow
inside its test and (b) a hard loss cap per trade are NOT met; it runs on paper by the owner's word. Nothing is proven
by this: both books stay tested losers until 300 live trades say otherwise. Same day (owner: "pretend like its a real
scenario"; interview answer "IBKR has shares"): the LIVE book now takes a short only when IBKR lends the shares it needs
(section 2, row "borrow"). That closes (a) for the live book only: the backtest still has no borrow in it, and (b) is open. Standing consequence: an alert book is never
left off silently; "off" is the owner's choice in the moment, and the phone must still show the system is alive.

**A trade that was texted is never cut (owner 2026-11-18 night).** "you can't cut trades that already happened in real
life ... we are trying to model this like actual real life with actual trades when we connect up real money." The
cause: GMM long 11-18 was texted bought $1.785 and sold $1.88, then the nightly summary read "GMM void" and it left
the book: the tested order did not fill on the official tape (the fill minute opened at the stop), and the code
dropped such a trade. Two longs (VTSI 11-13, GMM 11-18) and two shorts (NTCL 11-05, OMH 11-09) had been cut this
way, winners and losers alike. Rule: a trade the system texted as taken always gets an official result. When the
tested order has none on the full tape, the result is what the system DID at the prices that were really there: for a
long, bought at the first official print after the fill text, its stop and target resting on the full tape from
there, else sold where the live hold ended; a live red-candle exit = bought and sold at once (`sgt.fast.text_order`;
no exit rule is re-decided from the new price); for a short, sold at the first official minute after the live entry,
the rule's stop, 60 minutes (`fade_live.at_live_entry`). GMM under this rule (checked on the official prints, to be
booked by the settle): bought $1.835 at the text (not the texted $1.785), target $1.875 reached 11:16 ET, about
+1.4% after costs instead of the +5.3% on the phone. The stored reason says which way a result was made. Void is left only for a
trade that could not have happened: no trade at all on the full tape after the text. Nothing is switched off without
the owner saying so in the moment.

## 1. What we trade and why it works
Short small caps ($1-50) right after a MetaTradings momentum alert. Mechanism: retail-attention buying sprees in small
caps are followed by falling prices (Barber, Huang, Odean, Schwarz, "Attention Induced Trading and Returns"); the alert
marks the spree. Held-out result: **+1.12% per trade net of costs**, win 57%, n 764 (`prereg_alert_fade_2026-11-01`).

## 2. The algorithm today (exactly what runs)
| step | rule | source |
|---|---|---|
| entry | first 1-min bar at or after alert + 10 s (live: latest IEX trade; a print older than 90 s after a >= 10% move in 5 min looks like a halt and is not a price; without such a move it is a thin-on-IEX stock and is used) | prereg_alert_fade; RAYA / SNT fixes 2026-11-06 |
| filters | alert type in the 10 cells, 09:30-15:44 ET, $1-50, first event per symbol-day | prereg_alert_fade |
| stop | 20% above entry (worst case inside a bar). Live, the free feed can miss a move on a thin name, so since 2026-11-17 (owner decision 3A, `sgt.fast.tape_check`) the official one-minute bars, free once about 16 minutes old, are read every minute for each live short: an open trade whose stop the official tape crossed is closed there, and a trade covered later on the timer is corrected to the stop with one CORRECTION text. Estimates and texts only: the nightly official result was always computed from that tape | prereg_alert_fade (10/20/30/none explored on train); MPU 11-17 |
| exit | 60 minutes after entry, or the 16:00 close if earlier | prereg_alert_fade |
| beta (F7) | no short when the stock's beta >= 3 (owner 2026-11-10; past: >= 3 -2.14%/trade, < 1.5 +1.86%). The scanner followed the same rule on 11-10 and is paused since 11-11 (owner: longs first; it still records and scores spikes: digest line) Unknown beta still shorts: tested 2026-11-11, no-news unknown +1.03%/trade (n 367), not worse than beta < 3 (p 0.36) | fade.py, prereg_alert_fade F7; scanner.py, prereg_scanner_book A1 |
| repeat (F8) | no short on a stock the channel alerted 1-7 calendar days earlier (multi-day runner; owner 2026-11-12 deep review). No-news: kept +2.54% vs repeats -1.01% (train), +2.55% vs -1.33% (held-out). Longs still trade these alerts | prereg_fade_repeat_days_2026-11-12 |
| borrow (2026-11-16) | no short unless the IBKR lendable list stamped on the alert (snapshot at most 3 h old) shows at least the shares the trade needs (its dollars / entry price); none, too few or unknown = not taken, no text, row `filtered` with the reason. Checked at the tested entry after news / beta / price, before the switch. Still an event: F8 counts it, the long book trades it, the forward record scores it. Why: of the 22 settled paper shorts, 9 fail this rule: 7 had no shares at IBKR (and were not shortable at Alpaca; +$1,207 on paper) and 2 had fewer shares than the trade needed (CHSN 300 of 1,102; CISS 2,000 of 2,243; +$839). Paper counted +$2,046 no account could have earned; the 13 real ones lost about -$5,031 against the book's -$2,985. The tested average (+1.1%) was measured WITHOUT this rule (no borrow history exists for 2020-2026), so live-vs-tested compares the borrowable part to the whole | owner 2026-11-16 (interview: "IBKR has shares"); fade.no_shares |
| size | 5% of book NAV per trade by default; text `SIZE 10000` sets dollars per trade ($500 to 25% of the book; raising needs a code). Runaway guards only: 40 open, 200% of the book short. No daily cap or loss stop (F6) | fade.py, owner 2026-11-09 |
| **fade switch** | **ON again since 2026-11-16 12:13 ET** (owner: "we need to see how they perform"; section 0). Was OFF from 2026-11-12 night (owner, option A after the adversarial validation, finding 20). Flag `fade_off` (absent or "0" = on, anything else = off). The off check runs at the tested entry after the news / beta / price checks, so an off row is exactly a would-be short: no order, no text, F8 still counts it, the long book still trades it. The forward record keeps scoring every alert. Back on only by the owner's word, under section 0 | fade.py OFF_FLAG, owner 2026-11-12 |
| **long switch** | **MEASURE-ONLY from 2026-11-17** (owner, decision 1B, `prereg_long_nowin_2026-11-17`): the book watches every alert; the long it would have opened is texted once as "LONG SEEN, not traded" and stored as `seen`; no position, in no total; the nightly settle scores what it would have made. Was ON 2026-11-16 12:13 ET to 11-17 (owner; section 0): 4 trades on 11-17, about -$182. Was OFF from 2026-11-13 (owner, option A "measure-only"): the long book loses in every tested slice (A2 training -2.09% per trade, win 14%; every time-of-day bucket, setup and stop width) and the owner's bull flag and BoS rules failed too. Flag `long_off` (absent or "0" = on, anything else = off): no new longs, no texts; open longs still exit and settle; the nightly long shadow keeps scoring every alert. Back on only by the owner's word for a rule that passed its test | long_book.py OFF_FLAG, owner 2026-11-13 |
| drift guard | nightly: a live-monitor alarm (one-sided CUSUM k 0.5 / h 5 sd vs the tested expectancy, or win rate below tested at p < 0.01 after 20 trades) halves that book's size for NEW trades (floor 25%); never raises size, never closes a trade; restored only on the owner's word. Fade and long book. Since 2026-11-12 night it also judges the fade's FORWARD record (every alert, frozen rule, SIP; tested +1.12%, win 57.3%): an alarm there cuts the fade book. Read one session late (the record is written 21:20, the digest runs 20:50) | sgt.learning.live_monitor, owner 2026-11-12 |
| long book (paper) | first long setup to fill per alert WHOSE 2R TARGET BEATS THE ESTIMATED COST (2026-11-17, owner decision 4: 9% of tested trades could not win, 0 real winners; `long_book.no_win`); stop, 2R target, 30 min; since A2 (2026-11-12): size x min(1, 2.68% / stop distance) and out at the next open when the fill candle closes below the entry (tested -1.88% vs -2.29% per dollar: still losing) | prereg_long_risk_2026-11-12 A2 |
| long texts (2026-11-18) | a pending long order is texted once when its setup's last candle closes, before the fill (`sgt.fast.long_armed`); the fill text follows about a minute later as before. Nightly, the official price at each text is stored beside the booked fill (`sgt.fast.text_px`) and shown in the digest. Booking unchanged. | owner 2026-11-18, option 1; section 5 |
| how often it looks (2026-11-18, owner) | the alert channel is asked for new posts every 15 s during the session (`sgt.fast.chan_poll`; before, only Telegram's own timer, about 60 s: alerts reached the book a median 27.7 s late). Long book pass every 15 s (was 30). Official-tape stop check every 30 s (was 60). Short monitor every 10 s (unchanged). Flag `chan_poll_s` sets the channel interval (0 = off). | owner 2026-11-18 |
| scenario label (2026-11-18) | every alert is labelled nightly from official bars with its place in the move: RUN band (early up to 33.6% above the day's low, middle up to 63.1%, late above) and at / off the high (`sgt.fast.scenario`, `fade_trades.scen_*`); shown in the digest for each traded alert. A LABEL: no book reads it. It exists so scenario rules can be judged on new alerts (`prereg_early_alert_fade_forward_2026-11-18`: are fades on early alerts the losing third? decided once at 300 new alerts). | prereg_scenario_map_2026-11-18 |
| trader's matrix (2026-11-18, owner: "take the trader's advice to heart") | every long text (armed, entry, seen) carries the trader's trend matrix: green conditions out of six on the 1, 2, 3 and 5 minute charts (MACD 12/31/10, EMA 34, RSI 14, Bollinger middle, Supertrend 10/3, PVT 15) and what is red on the 1-minute one (`sgt.fast.trend_matrix`). His rule is tracked on every long of the day, traded or watched, also after the book's own exit: when MACD turns red after having been green since the entry, or at the close, one text "TRADER'S RULE" with his exit price, stored in `long_trades.tr_exit_*` and shown in the digest beside the book's exit (`sgt.fast.trader_rule`). The book's fills, exits and totals do not read it. Free-feed candles: estimates. | owner 2026-11-18; prereg_trend_book_2026-11-18 |
| no cut trades (2026-11-18, owner) | a texted trade whose tested order has no official fill is scored as the order a real account would have sent at the text (long: first official print after the fill text, its stop and target on the full tape, a live red exit = out at once; short: first official minute after the live entry, 20% stop, 60 minutes), costs as always; void only when the full tape has no trade after the text. | section 0 |
| approval | not ARMed: enters at the tested time, text carries "NO <code> = exit now" (F5) | prereg_alert_fade F5 |
| confidence | 100 x P(trades like this are profitable on average): news x time of day, 38-99 | prereg_fade_news A2 |
| texts | trade card, news, SEC filings, StockTwits buzz, X buzz (counts exact; newest-10 sample; <= $0.055 per alert), Reddit buzz (ApeWisdom 24h mentions, free), chart read, borrow, TradingView link | fade_texts.py, xsocial.py, reddit.py |

## 3. What actually drives results (the knowledge)
1. **News kills the fade.** News-type alerts or company news: +0.00%/trade train (n 1,107), -0.37% held-out (n 277).
   No-news alerts: +1.84% / +1.97% and all of the profit. Real news gives buyers a reason; hype without news reverts.
   (Owner decision 2026-11-06: keep trading news alerts; confidence shows 38-66 for them.)
2. **Time of day.** Per trade: before 11:00 +0.58% / +0.39%; 11-14 +1.63% / +1.89%; after 14:00 +2.33% / +4.04%
   (train / held-out). Morning alerts are two thirds of the trades and the weakest.
3. **The drop starts at the alert and plays out over about an hour.** 15 min +0.18%, 30 min +0.65%, 60 min +0.97%,
   90 min +1.14%, 2 h +1.05%, to the close +0.75% (train). Entering 2-4 minutes late loses most of the edge.
4. **Squeeze-then-fade.** These stocks bounce hard inside the hour and then fall again. Any rule that reacts to a
   bounce (trail, VWAP, EMA cross, failed pullback, breakeven) covers right before the second leg down.
5. **Re-alerts mark the squeeze top.** A third of fades get re-alerted during the hold (on new highs); they lose
   -4.6% if held but -5.9% if covered at the re-alert. A repeat alert an hour later means a sustained run: fading
   it loses -3.19%/trade, and buying them lost -2.43% on the sealed test: no stable direction. Trade the first
   alert only.
6. **Hindsight always finds an exit.** 84% of fades show at least +3% profit at some point in the hour (75% show +5%).
   A chart review of any loser will find a "best exit"; that is not a rule.
7. **Underwater trades that come back usually keep falling.** Fades that went 5% against us and returned to the entry
   then earned +2.30% (train) / +4.21% (held-out) more by holding.
8. **The stop is a trade-off, not a free fix.** No stop +2.31%/trade on train but with -78% worst cases; 10% stop
   +0.74%, 20% stop +1.47%, 30% stop +1.74% (exploration on train): a tighter stop halves the edge. About 25% of fades lose more than 15%. RAYA (2026-11-06) was stopped at the top of
   its squeeze and fell back below our entry 15 minutes later.
9. **Borrow is the real-money question.** Names Alpaca can short lost -0.55%/trade held-out; live stamps show the
   biggest winners (CRIS, AEHL) had zero IBKR shares. Decided at forward n >= 300 on the IBKR-borrowable subset.
10. **Chart signals carry no edge here.** EMA 9/21 cross (46,711), RSI, MACD, PSAR, PVT, VWAP, the trader's full
   setup, 30+ signals in a learned model: about 0 before 0.7-1.4% costs. Shown in texts, never filters.
   On alert longs the trader's five rules pick WORSE trades: all true -3.13% vs -2.02%
   (`prereg_trader_rules_long_2026-11-12`). 9/21 cross at its EXACT price (69,720): -0.87% net; on prints
   with 3x volume (7,608): -0.71%; a minute later: 0.05% (`prereg_ema_instant_volume_2026-11-18`).
   Shorting while EMA 9 > EMA 21 IS the fade: 92-97% of alerts have 9 > 21 at entry, and those shorts made +0.73% /
   +0.94% (no-news +1.42% / +1.31%) train / held-out; buying there is the losing side (-1.65%, L1).
11. **The edge is the channel's selection, not the price pattern.** Spikes we detect ourselves (5% in 5 min, 5x
   volume, new high; 517 in training) fade only +0.11% before costs, -1.23% after; the 4% the channel also alerted
   earned +4.7% (`prereg_self_fade_2026-11-06`). The book depends on that channel staying as it is.
12. **Price matters, and it is not where you would guess.** By entry price (train / held-out): $1-2 +2.4% / +2.1% per
   trade (about two thirds of all profit), $2-5 +0.2% / +0.2%, $5-10 +0.9% / +1.2%, $10-50 +0.3% / +0.8%. A $0.02/share
   locate still leaves $1-2 fades at +1.4% / +1.2%. Descriptive (found after looking), so forward-only if used.
15. **Jumpy beats calm.** Pre-alert 1-minute volatility (last 30 bars) sorts fades cleanly: calmest fifth -1.32% /
   -1.18% per trade (training / held-out), jumpiest fifth +3.64% / +4.86%; holds in every time-of-day bucket, price
   band and news group, 15 of 18 months. Found after looking -> forward shadows `calm skip` and `vol up`
   (`prereg_fade_calmskip_2026-11-08`). Inverse-volatility sizing (the textbook version) is rejected.
19. **Two red weeks mean nothing; check for look-ahead first.** Even with the edge intact 36% of 2-week windows are
   negative (45.5% of days). A promising split (entering at a fresh high: -0.59% vs +1.99%) used the entry bar's own
   high, which happens after the entry: with point-in-time data it vanished.
20. **November 2026: the fade stopped working, and it is not a bug.** Forward record (every alert, same code and same
   channel-history source as the held-out test) Nov 2-12: **-3.44%/trade, win 39%, n 61**, against 57-58% wins in
   every held-out month; a 9-session stretch of held-out days is this bad 1.1% of the time (session bootstrap). Live
   paper agrees (21 trades, -3.39%). Adversarial validation 2026-11-12 night: an independent re-implementation from
   raw data reproduced held-out 764/764 and November 61/61 trades exactly; a red-team found no look-ahead or
   survivorship bias >= 1%/trade. November alerts look the same (type,
   price, cost, timing); only the outcome flipped: gross +2.52% -> -1.60%, stopped 25% -> 33% (no-stop hour still
   +2.33%). The held-out mean was thinner than it looked: September -0.15% after costs, the +1.1% rests on July and
   October. Reading: real-but-gone or a long bad patch; undecidable at n 61. The live drift guard missed it because it
   watched 7 live no-news trades; on the forward record its win-rate test would have alarmed on 11-10. Action: shorts
   OFF 11-12, ON 11-16 (section 2); guard: forward record (6).
18. **Real stop fills cost ~1% per stop; only no-news alerts survive them.** SIP trades at every historical stop:
   slippage +0.82% / +1.44% mean (median ~0.35%). Net with real stops: no-news **+1.70% / +1.77%** (lower bounds
   +0.88% / +0.47%), news **-0.20% / -0.60%**; with a 1.5% locate no-news stays +0.70% / +0.77%
   (`prereg_fade_stop_slippage_2026-11-09`). Longs on the same alerts all lose (`prereg_long_family_2026-11-09`), including the trader's setups:
   first red candle, bull flag breakout, break of structure, halt reopen (`prereg_long_setups_2026-11-10`).
17. **Halts right after the alert were a live-only loss.** The tested rule enters at the first bar after alert + 10 s
   within 5 minutes; a halt means no such bar, so the backtest never traded those alerts, but the live book shorted
   the last pre-halt print. Those events: -4.9% / -7.4% per trade, median -21% (5-8% of alerts). Corrected for live
   semantics the pooled edge was +0.86%, not +1.01%. Fixed 2026-11-09: live entry = tested entry (OMH review,
   `prereg_fade_halt_audit_2026-11-09`).
16. **Our cost model matches real spreads.** Real quoted bid/ask at entry and exit for 2,925 tested fades: round trip
   0.85% / 0.87% vs 0.90% / 0.89% modelled; net +1.18% / +1.33% at real spreads.
   The edge is not a cost illusion; borrow/locate is the open cost question.
   Resting limit exits do not beat the spread: fills save +0.58%, misses cost -0.59% (queue model, finding from
   `prereg_fade_passive_exit_2026-11-08`).
14. **Losing streaks do not predict the next fade; do not stop after losses.** After the day's last two known results
   were losses, the next fade earned +2.18% (training, n 205) / +2.53% (held-out) vs +0.86% otherwise; a daily breaker
   costs ~1.3% per session. (`prereg_fade_dayflow_2026-11-08`).
13. **Sizing by a model's ranking may help** (dev +2.37%/session, held-out secondary +8.31%), but forward evidence so
   far is negative (n 34). In shadow until n >= 300.

## 4. Rules tested and rejected (do not re-test without a new reason)
| idea | train per trade (champion +0.97%) | held-out (champion +1.12%) | file |
|---|---|---|---|
| trailing 5% stop | -0.18% | -0.50% | prereg_fade_exits_2026-11-05 |
| two-line exit (11-17): cover X% off the low while under the entry; X 5/10/15% | -0.21 / +0.40 / +0.47% | -0.57 / -0.24 / +0.32% | prereg_fade_profit_lock_2026-11-17 |
| half-time cut (11-17): leave a short losing at 20 / 30 min | +0.91 / +0.86% | +1.00 / +0.97% | prereg_fade_time_cut_2026-11-17 |
| timing maps (11-17): leave a winning short once its low is 5-20 min old; LONG 0-90 min after the alert | +0.29..+0.84%; longs -0.95..-3.17% | not opened (0 of 39 cells) | prereg_timing_maps_2026-11-17 |
| skip a stock already alerted before the open that day (11-17) | fresh +2.81% vs warm +1.63% (p 0.24) | not opened | prereg_fade_premarket_warm_2026-11-17 |
| sell limit 2-10% above the first print, else market | +0.53% .. -0.36% | +0.19% .. +0.32% | prereg_fade_limit_entry_2026-11-17 |
| enter at alert + 10..60 s instead of the next bar | +0.49% .. +0.82% | not opened | prereg_fade_entry_seconds_2026-11-17 |
| LONG from the alert day's close, overnight to 5 days | -1.1% .. -6.3% | not opened | prereg_alert_after_day_2026-11-17 |
| regime gate: no fades after a poor prior 5 sessions (win share < 50%, or mean net < 0) | skipped trades -0.24% / +0.47% (n 511 / 924): gate +0.05% / -0.19% per trade (p 0.36 / 0.83); sessions have no memory (lag-1 corr +0.06) | not opened; Nov 2026 it would skip 5 of 10 sessions (skipped -4.7%) | prereg_fade_regime_gate_2026-11-13 |
| LONG: owner's Clean Bull Flag Finder (exact port, EMA 9 > 21, whole session after the alert); 2R / 30 min, or hold while 9 > 21 | -1.60% / -1.78% (n 5,250 / 4,661; stopped at the flag low 55%); strength >= 75: 68 trades, noise | not opened; Nov 2026 -1.42% / -1.22% | prereg_bull_flag_finder_2026-11-13 |
| LONG: break of structure (pivot 5) while EMA 9 > 21; same two exits | -1.45% / -1.56% (n 5,017 / 4,531). Next bar after a BoS: up 1%+ 21.4% vs 17.3% usual, mean +0.08%: true but a tenth of the cost | not opened; Nov 2026 -0.13% / -0.04% | prereg_bos_ema_2026-11-13 (A1: the owner's own Pine script, same result -1.45% / -1.59%) |
| cover on VWAP reclaim | +0.52% | +0.22% | same |
| cover on EMA 9/21 cross up | +0.68% | +0.99% | same |
| 15% profit target | +0.23% | +0.37% | same |
| hold 2 h / to close | +1.05% / +0.75% (n.s.) | +0.98% / +0.63% | prereg_fade_hold_2026-11-05 |
| learned exit (model at 10-50 min) | +0.81% (vs +0.92% same window) | - | prereg_fade_exit_learn_2026-11-06 |
| wait for breakdown + failed bounce | -0.08% | +0.24% | prereg_fade_confirm_2026-11-06 |
| cover when a pullback fails | +0.87% | +0.07% | same |
| breakeven exit after 5% underwater | +0.38% | +0.01% | same, addendum B |
| half at +3% / +5%, rest at breakeven | -0.50% / -0.16% | -0.67% / -0.48% | same, addendum C |
| enter 2 / 4 minutes late | +0.14% / -0.21% | +0.28% / +0.61% | prereg_fade_challengers_2026-11-04 |
| skip-filter model v1 | total profit not higher | - | prereg_fade_meta_2026-11-04 |
| SEC filings split (8-K today, offering 30 d) | no reliable split | flips sign | prereg_fade_filings_2026-11-05 |
| fade the channel's later alerts (repeat after 60 min) | -3.19% (n 402) | not scored | prereg_fade_later_alerts_2026-11-06 |
| BUY the channel's later alerts (from the short result) | +0.92% (one month = 61%) | **-2.43%** (n 147, sealed test) | prereg_later_long_2026-11-06 |
| cover when the channel re-alerts during the hold | +0.56% | +0.75% | same, addendum A |
| skip fades when small caps are risk-on (IWM >= +1% at entry) | risk-on fades did BETTER (+1.50% vs +0.94%) | n.s. | prereg_fade_mood_2026-11-06 |
| inverse-volatility sizing (smaller in jumpy names) | +0.17% per $ vs +0.97% | +0.37% vs +1.12% | prereg_fade_volsize_2026-11-08 |
| timed exit as a limit at the bid (2 min, then market) | queue model -0.00% (bars said +0.25%: fill artifact) | -0.06% | prereg_fade_passive_exit_2026-11-08 |
| LONG every alert (5% stop, +5% target, 15 min) | -1.65% (51% stopped) | -1.72% | prereg_long_family_2026-11-09 |
| LONG calm alerts (the quintile that loses as a short) | -0.87% | -1.11% | same |
| LONG the reopen after a halt | -2.12% (63% stopped) | -3.52% | same |
| LONG first red candle (next open / break of its high) | -1.19% / -2.44% | -1.27% / -2.36% | prereg_long_setups_2026-11-10 |
| LONG the bull flag breakout (5% pole, 2-4 bar flag, >= 2 red, <= 50% retrace, lighter volume) | -1.72% (gross -0.82%) | -1.55% | same |
| LONG a break of structure (close over the last swing high after a higher low) | -1.85% | -2.72% | same |
| LONG the first red candle after a halt reopen | -1.87% / -2.83% | -0.19% / -7.01% | same |
| SHORT the failed bull flag (break below the flag low) | -1.38% | -0.74% | same |
| bull flag v2 (owner-graded detector: pole >= 10%, no gaps, new high of day, flag lighter than the push): LONG breakout / SHORT failure | -2.05% (n 199) / -1.85% (n 556) | not opened | prereg_flag_v2_2026-11-10 |
| BUY a sudden drop we detect (-5% in 5 min, 5x volume, new low; 20% stop, 60 min) | +0.32% (gross +1.07%; lower95 -0.39%, 2nd half -0.09%, top month 57%) | sealed | prereg_flush_long_2026-11-10 |
| BUY a big sudden drop (>= 10% in 5 min; idea from the row above) | +1.06% (seen, n 95; 2nd half -0.72%) | **-2.15%** (n 87, decided here) | prereg_big_flush_2026-11-10 |
| LONG book: sell half at the 2R target, trail the rest under the latest swing low (owner's GEVO note) | -2.42% vs live -2.29% (n 2,242; diff p 0.97); whole runner -2.55%, trail from entry -2.40% | not opened | prereg_long_runner_2026-11-11 |
| LONG book gated by the trader's five rules (9/24 EMA, 5x volume, MACD hist > 0, PSAR green, above VWAP) | kept -3.13% vs skipped -2.02% (n 546 / 1,696; p 0.997) | not opened | prereg_trader_rules_long_2026-11-12 |
| LONG the trader's own entry on alerts (fresh 9/24 cross + the other four, 30 min) | +0.60% but n 15 (p 0.075 vs book; no stop -0.67%) | not opened | same |
| LONG book: hold a 2R winner while MACD is positive (trader on GEVO) | -2.50% vs -2.29% (p 0.96); target trades +7.41% vs +8.78% | not opened | same |
| LONG book: skip stops wider than 7.09% (owner, POAS 11-12) | kept -2.01% vs skipped -3.44% (p 0.033 > 0.0125) | not opened | prereg_long_risk_2026-11-12 |
| LONG book: size by risk, w = min(1, 2.68% / stop distance) | per $ -1.96% vs -2.29% (p 0.004) | -1.96% vs -2.37% | same: passes, still losing |
| LONG book: sell at the next open when the fill candle closes below entry (owner, POAS/ZDGE) | -2.09% vs -2.29% (p 0.007) | -2.22% vs -2.37% | same: passes, still losing |
| LONG book: both together (risk size + red-candle exit), adopted 2026-11-12 | per $ -1.88% vs -2.29% (p 0.002) | -1.91% vs -2.37% | same, A2: ADOPTED, still losing |
| LONG book (A2): breakeven stop after +1R (owner, RETO 11-12) | per $ -1.86% vs -1.88% (p 0.12; 162 of 2,242 changed) | not opened | prereg_long_breakeven_2026-11-12 |
| LONG book (A2): only on repeat alerts (multi-day runners) | per $ -1.78% vs first-time -1.91% (p 0.20) | not opened | prereg_repeat_regime_2026-11-12 R1 |
| fade (F8 kept): skip after hot-tape days (prior session > 9 alerts) | hot +1.72% vs not +2.90% (p 0.14); hot still profitable | not opened | same, R2 |
| fade (F8 kept): skip 5-day price runners (> +14.2%, split-adjusted) | runners +2.69% vs kept +2.49% (p 0.57); without F8 runners lose, and 77% of them are F8 repeats | not opened | prereg_price_runner_2026-11-12 |
| LONG book: skip fills opening > 1R above the trigger (RETO) | 1 of 2,242 on SIP bars: an IEX-bar effect, not a rule | - | same |
| fade: cover when a bull flag breaks out (buy-stop at the flag high) | +0.09% per fade vs plain | +0.05% (p 0.38; no-news 0.00%) | prereg_fade_flag_2026-11-10 |
| fade: skip when a bull flag completed in the 15 min before the alert | skipped -0.87% (p 0.17) | skipped +2.91% | same |
| stop for the day after two losses | skipped trades +2.18% | +2.53% | prereg_fade_dayflow_2026-11-08 |
| skip after >= 3 fades in 30 min | +0.07% skipped (n.s.) | +1.65% skipped | same |
| fade our own spikes (no channel) | -1.23% (n 517) | sealed | prereg_self_fade_2026-11-06 |
| other: trader rules, ORB, crypto C1/C2/C2M, earnings, large-cap intraday | all FAIL | | docs/findings |

## 5. Loss reviews (each: facts, classification, lesson, test)
| trade | facts (official bars) | classification | lesson / action |
|---|---|---|---|
| RAYA 2026-11-06 -20% | alert during a LULD halt; live "entry" $1.425 was a stale print, real reopen $1.52; stop at the squeeze top, back below entry by 10:01 | execution bug + news trade + stop trade-off | stale-quote guard shipped; 3 exit/entry ideas tested, all lose |
| SERV 2026-11-06 -2.4% | +2.8% at 10:33, ground up, 60-min exit | rule loss, news trade | partial-profit idea tested: -0.50%/trade |
| STAK 2026-11-06 -2.3% | +4-8% for 40 min, rebound at 11:07 as the timer fired; rose to -9.6% after our exit | rule loss (timer saved a larger loss) | same |
| OMH 2026-11-09 -23% (live est) | alert 09:38:18, live short $27.04 at 09:38:33; LULD halts 09:39-09:47 and 09:55-09:58; reopened $32.80 over the $32.45 stop; live cover $33.33 (1-min monitor after the halt). Officially void (no tested entry bar within 5 min). Not shortable at IBKR / Alpaca | execution divergence (live entered where the rule cannot) | live entry aligned to the tested bar; halt audit: such events -4.9% / -7.4%; "Confidence 98" relabelled as win chance 57% |
| NWGL 2026-11-09 -20% (live est) | alert 10:25:37; short $4.38 at 10:26:31 (54 s); already +15% in 5 min, 14% over VWAP, RSI 75, 5x volume; stopped 6 min later; no halt; IBKR 0 shares | rule loss (stop), no-news | none new: 'wait for a breakdown' tested -0.08%/trade |
| FISN 2026-11-09 -20% (live est) | live entry $2.32 was a STALE IEX print: SIP traded $2.41-2.44 that minute; the live stop ($2.78) came from it (tested stop ~$2.92) | execution (stale thin-IEX price) | live entry now needs a print inside the tested bar (F6 branch); IEX-only stops lag on thin names -> paid SIP feed before real money |
| CKX 2026-11-09 +15.6% (live est) | news trade (company news), spike failed and broke down | rule win, news | no-news rule (F6) would have skipped it; news fades -0.20% / -0.60% over 1,384 trades; partial-profit idea tested -0.50% |
| AMIX 2026-11-10 -21.8% fade and -21.8% scanner (official SIP) | alert 09:42:25, no news; shorted 09:43 (fade) and 09:44 (scanner spike); stopped. Beta 4.8 | rule loss under the rules then live; rule F7 (no shorts at beta >= 3) went live 11:45 the same day and the scanner had no beta rule | F7 would have skipped the fade; scanner amendment A1 skipped it too, then A2 paused the scanner (owner: longs first; spikes still scored nightly) |
| RETO 2026-11-12 short -21% (live est) | alert 09:46:49 (resumed); short $8.10 at 09:47:43, a real post-alert print inside the SIP 09:47 bar ($8.00-9.12), not a pre-halt print; then 5 LULD halts in 40 min, stop | rule loss (stop); resumed alerts are the BEST fade cell (+3.16% held-out, n 218) | none: skipping resumed alerts is contradicted |
| POAS 2026-11-12 short skipped | SIP traded 981k shares in the 09:36 entry bar; live saw no usable IEX print until the 90 s stale guard (halt 09:37); tested short would have stopped ~-20% | live/test divergence (saved a loss) | log quote-attempt times [VERIFY cause] |
| Longs 2026-11-12: ADXN, XLAB, POAS, RETO | live IEX est +6.0 / +5.9 / -13.2 / -10.7%; official SIP replay -3.0 / -4.3 / -13.2 / +11.0% (ADXN, XLAB same-bar stops on SIP; RETO filled 8.82 on SIP, not 9.33) | book as tested (-2.29%); IEX texts disagree with SIP per trade on halting names | lessons tested: prereg_long_risk (risk sizing and red-fill-candle exit pass, still losing) |
| XLAB 2026-11-12 short -20.9% (live est, ~-$1,045) | alert 11:43:10 (gaining_momentum); short $1.29 at 11:44:13 (tested: the 11:44 open $1.28); a 45-minute grind up on rising volume to $1.58, stop 12:29; borrow fee 481%, dilution filing flag, 18.5% over VWAP, RSI 80, EMA 9 > 21 | rule loss (stop): the rise came after the entry | the note's lessons are tested: exit when price reclaims the entry (+0.87% / +0.07% vs champion), tighter stop (10% halves the edge), dilution filings (flip sign, prereg_fade_filings); EMA 9 > 21 at entry is where the fade's profit is (finding 10) |
**Owner notes review (2026-11-11; all 20 notes, trades 11-06 to 11-11).** The notes keep proposing the same five ideas.
Each one is matched to evidence below:
| idea in the notes | trades | evidence |
|---|---|---|
| short only after the spike fails: support break, then a failed bounce | FISN NWGL OMH RAYA STAK x2 TVRD CKX(scan) ZDGE | tested and lost: -0.08% / +0.24% vs +0.97% / +1.12%; entering 2-4 min late also lost |
| take some profit into the first sharp drop, protect the rest | CKX SERV STAK NWGL(scan) CISS TTEC ZDGE AMIX | tested and lost: half at +3% / +5% -0.50% / -0.16%. Half at a target with the rest on the timer averages the target exit (15% target +0.23%) and the timer, so it is below the timer by construction |
| exit when the first pullback fails or price retakes the earlier high | AMIX x2 RAYA OMH NWGL STAK FISN | tested and lost: +0.87% / +0.07%; findings 4 and 7 |
| the 20% stop is too wide | NWGL OMH | 10% stop +0.74% vs 20% +1.47% vs 30% +1.74% (training, prereg_alert_fade); a tighter stop halves the edge |
| LONG: sell part at the 2R target, let the rest run, exit on a break of the last pullback low | GEVO | tested 2026-11-11 (owner OK) and lost: -2.42% vs -2.29% per trade; the trail gives back more than the move adds (prereg_long_runner) |
| LONG: only buy when 9/24 cross, volume spike, MACD+, PSAR green, above VWAP; hold while MACD positive (the trader, via the owner, GEVO) | GEVO | tested 2026-11-12 and lost: all five true picks worse longs (-3.13% vs -2.02%); his entry almost never fires after an alert (15 training trades vs the book's 2,242); holding for MACD -2.50% vs -2.29% (prereg_trader_rules_long) |
| LONG: wait for the pullback to hold, then buy the turn up | ZDGE (long) | = L4b / L6 (break of the red candle's high, break of structure): tested and lost |
The notes also flagged data and execution issues:
| issue | finding | action |
|---|---|---|
| STAK 11-10 short: IBKR 0 shares, Alpaca not shortable | paper-only result | the Notes page flags it; the real-money call is on the IBKR-borrowable subset (finding 9) |
| AMIX 11-10: posts describe a patent catalyst while company news = none | social catalyst outside the news check; Sol flagged the same | X catalyst posts recorded forward (prereg_fade_x A2) |
| CISS: chart ends at 10:24, exit at 10:32 | intraday SIP bars lag about 16 min | the page now says where the candles end |
| TVRD: live +0.7%, official -0.96% | round-trip costs plus SIP prices | the page labels live estimate vs official result |
| GEVO: $1.975 sell vs $1.96 target | the fill bar traded the target AFTER the buy (it crossed the trigger first), but the simulator skipped it and took the next bar's $1.975 open | **fixed 2026-11-11** (owner OK): the fill bar sells at the target in patterns.simulate and long_book._exit. On the 6,529 historical setup fills 29 change (23 target fills drop to the target price, 6 later stop-outs become target wins); every setup's mean moves <= 0.01%, no conclusion changes |
| ZDGE long: entry and stop in the same minute | the simulator counts it as a full stop (conservative), as tested | none |
| AMIX: $2.81 stop, $2.92 live cover | live IEX one-minute stop monitor; official $2.784 (SIP stop level) | known (FISN row): IEX stops lag on thin names |
| XLAB 11-12, owner's chart (night) | "easy 10%"; EMA 9 stayed above 21 all afternoon; two bull flags (owner's Clean Bull Flag Finder, strength 77.19 near $1.80 and 86.11 near $2.25). Facts: flat/down 09:30-11:00 ($1.05 -> $1.00), run from 11:15, alert 11:43 at $1.28, high $2.50 at 14:15; our LONG book bought it too (L4b $1.36, stopped $1.32, -5.0%) | both books lost on a +95% day: the short by design, the long on a tight stop at the first dip | shorts OFF (finding 20); the owner's indicator is pre-registered as a long setup watching the whole session (prereg_bull_flag_finder_2026-11-13); BoS / CHoCH next once its Pine source arrives |
| Longs 2026-11-13: SLSN -2.4%, FCUV -11.4% (live est); FCUV short +4.5% (last short before the switch) | SLSN: alert 09:33, red-candle-close long $1.27 at 09:35 one bar after the $1.35 spike high, stopped $1.24 in the same minute, stock $1.05 by 09:41. FCUV: halt-resume alert 09:57 while the reopen bar fell $7.92 -> $6.59; long $6.205 at 09:59, stop $5.66 was crossed on SIP at 10:02 (low $5.28) but IEX had no bar 10:00-10:03, so the live book sold $5.50 at 10:23; the official SIP result is the stop ($5.66, -8.8%). A2 risk sizing booked FCUV at 1.5% of the book instead of 5% (loss ~$170, not ~$570) | long book as tested: A2 expects -2.09% per trade and a 14% win rate; in training it loses in every time-of-day bucket (-1.8% to -2.4%), every setup, every stop width | no rule change: no slice of the long book is positive, and the owner's bull flag and BoS rules also fail (section 4). Known data limit: live exits see IEX only; a stop can show 20 minutes late on thin names, the official result uses SIP |
| 2026-11-13 notes review (all four trades, Luna notes; no owner notes) | SLSN long -2.4%: entry, high and stop in one minute, "no change follows". FCUV long -11.4%: note asks why the $5.66 stop crossed at 10:02 was sold at 10:23 (answer: no IEX bar 10:00-10:03; official result uses the stop) and flags the 8.8% stop (wider-than-7.09% skip was tested: FAIL p 0.033; risk sizing already cut it to 1.5% of the book). VTSI long -0.5%: bought as the spike reversed, 2-cent stop; note asks if so tight a stop is realistic (history: the tightest stops lose least, -1.44% vs -2.3% and worse). FCUV short +4.5%: note says IBKR and Alpaca both showed NO shares to borrow, so the gain was never tradable | rule losses on a book that is now measure-only; the one win was not executable | nothing new to test; the borrow note is section 0 in practice |
| 2026-11-13 official results (20:50 digest, SIP) | SLSN long -4.0% (in $1.275, stop $1.24; the live estimate -2.4% left out costs, large on a $1.27 stock). FCUV long -9.5% (in $6.20, stop $5.66: -8.7% before costs, as predicted from the SIP bars; the live $5.50 sale at 10:23 is not the record). VTSI long void (the live order never filled on SIP bars). FCUV short +11.6% official (SIP entry $6.86 vs the texted $6.29), opened 09:57, 58 minutes before the fade switch was set (10:55 ET); no shares to borrow at IBKR or Alpaca, so the +$557 in the day's total was never tradable: day -$1,164 as printed, -$1,721 without it | nothing new to test: all three longs are the A2 book (off since 11-13, measure only); the short is the fade (off) | digest: a book that holds nothing now prints "$0 today, flat since the <date> close" instead of "no close yet" (megacap made no calls on 11-12 and 11-13) |
| 2026-11-13 full review of every book (owner 11-14: "analyze every single one"; news checked by web search) | megacap 15 -$730 = Thursday's five calls held close to close: AMD long 13% -8.75% (-$1,173), AAOI long 7% -5.60% (-$402), TSLA short +$399, JPM long +$253, ORCL short +$193. Nvidia CEO resignation reported 11-12; AMD/AVGO rallied on it, then the chip group sold off 11-13 (SOX about -3.4%, 10-year yield 5%); AMD had no news of its own. Six-name megacap: all six forecasts inside the no-trade band, flat. ETF books -$679: no decision made (monthly rebalance); SPY -0.36%, IEF -0.19%, IWM -0.32%, DBC -0.39%. Alert longs: SLSN (record Q3) in $1.275 stop $1.24 same minute, low -20% in 7 min, close +19% vs entry; FCUV in $6.20 stop $5.66, low -16%, close +14%; VTSI (earnings beat) in $3.82, 2-cent stop, 4-cent target, low -6%, close +11%, void. FCUV was long AND short in the same minute | the code followed its rules in every case. Rule faults: (1) the daily model sees price/volume only: no event, no same-theme overlap check; (2) it trades a forecast that is high vs the stock's past forecasts, not vs its risk today (AMD +1.25% forecast vs 7.4% daily swings); (3) the alert long buys the first red candle after the spike, inside the 60 minutes where the fade test says these stocks fall; (4) stops/targets smaller than costs (VTSI target 1.0%, round trip about 1%). "All three closed higher" is not a rule: news-name longs, later-alert longs, earnings continuation, opening-range on stocks in play all FAILED before; the short held to the close still made +0.63..0.75% | to test on 2018-26 history: forecast / current risk gate; same-theme cap; no new long into a name's earnings. To build: one view per stock (never long and short together); no trade when target minus costs <= 0 (both part of the money-based score); digest headline separating yardstick books; texted estimates after costs |
| 2026-11-14 owner: numbers that did not match (page Friday -$100 on 4 trades vs Saturday +$216 on 3; midnight summary -$823 vs digest -$1,164) | page: until the 20:40 ET settlement it shows live estimates (free feed, before costs): SLSN -$117 -> -$198 official, FCUV long -$172 -> -$143, VTSI -$26 -> void, FCUV short +$215 -> +$557 (official entry $6.86 = the open of a bar that fell to $5.66; the live price was $6.29). Summary: it left out the alert long book (-$341) | the official entry on a fast halt-resume bar is not reachable by a real order; the digest already tracks this ("texted vs official") and it argues for trusting the estimate on such bars | fixed: summary includes the long book, uses the digest's book names and total; the page tile says "live estimate, official after 8:40 pm ET" until results are official. Open: an honest official entry on halt-resume bars (needs a prereg amendment, the fade is off) |
| Power hour 2026-11-13: no model trades | six-name set decided at 15:50: all six flat (a normal outcome). 15-name set: NO decision: RDDT had 186 one-minute bars on the free feed, under the 195 floor, and one name crashed the set (a failure text was sent). The paper book kept the 11-12 positions | infrastructure bug, not a model call | **fixed 2026-11-13**: live floor 60 bars + a print in the last 30 minutes; an unusable name is flat with its reason, the rest decide (amendments in the three megacap pre-registrations) |
| RETO 11-12 "pre-halt entry" and XLAB 11-12 "timestamp conflict" (Luna) | both false: the review saw IEX bars only (RETO's 09:47 entry bar had no IEX prints) and converted UTC with the summer offset (16:53 UTC is 11:53 ET in November) | **fixed 2026-11-12**: reviews get ET times and SIP bars (IEX only as fallback), and wait until SIP covers the trade |
Since 2026-11-11, Luna writes in this format and checks every idea (its own and the note's) against sections 3-4. The result is labelled "tested: lost", "untested" and so on. Luna re-reads a note once it is saved.

Process: `docs/runbooks/loss_review.md`; facts: `scripts/loss_review.py SYMBOL DATE`.
AI reviews: since 2026-11-10 the per-trade review, the nightly brain and the Notes tab include the long book (before, longs never reached them).
Long book official result (2026-11-11, owner: "real long scoring"): the live trade's OWN order (fill bar, buy-stop trigger, stop) replayed on SIP bars by the tested simulator, like the fade. Before, it was the shadow's SIP re-detection, which can be a different trade: KULR 11-10 lost live (IEX trigger $2.155, stopped -1.2%) but was recorded +1.63% (SIP trigger $2.115). Old rows are re-scored once. The long shadow stays as the rule's own measurement, so live vs tested = execution gap.

**2026-11-16, the morning both alert books were off (official numbers).** 31 alerts, no trade: books off until 12:13 ET,
and the 4 alerts after that failed the rules (repeat, news x2, price). The research record took 8 fades that day:
HIHO -21.8% (stop), GDHG -21.8% (stop), SLSN +7.5%, GP +8.9%, RCON +26.2%, NOMA +35.7%, TDTH +37.6%, FGL +38.6%:
average +13.9%, 6 of 8 won (record now 81 trades, -0.25% per trade, win 48%). The paper book with its rules on all day
would have taken three of them: GDHG -21.8%, RCON +26.2%, TDTH +37.6% (HIHO, SLSN and NOMA are news names; GP and FGL
had no shares at IBKR): about +$1,050 at the halved size. The long shadow lost on every setup that filled that day
(9 alerts, first setup each: -1.5% to -14.4%, average about -4.5%). One day: no rule changes on it. GDHG ran from $1.87
to about $7 through halts; the 20% stop is the only cap a short has (section 0, condition b, still open).

**Where every dollar went, all books, start to 2026-11-16 (owner that night: "why are we still losing money ... figure
out exactly what's going on"; ledger pulled read-only from the runner).**
| book | since | result | what it is made of |
|---|---|---|---|
| megacap (6 names, long and short) | 11-02 | +$4,435 | long calls +$3,624 (10 stock-days, 8 won), short calls +$1,140 (5, 3 won) |
| megacap 15 | 11-10 | +$1,224 | long calls -$640 (12 stock-days, 5 won; AMD -$1,916), short calls +$2,063 (6, 5 won) |
| long calls (trading book) | 11-16 | -$3 | one entry, cost only |
| overlay (holds the six) | 11-09 | -$2,866 | a held basket: NVDA -$1,267, AMZN -$568, MSFT -$475 |
| 60/40, dual momentum, 60/40 trend | 11-03 | -$1,555, -$3,204, -$1,239 | held baskets, no decisions: SPY, DBC, IWM fell |
| alert shorts (fade) | 11-04 | -$2,985 on 22 trades | 5 stops -$5,388; the other 17 +$2,403 (+2.8% each). The 13 a real account could borrow: -$5,031, 3 won |
| alert longs | 11-10 | -$1,212 on 10 trades | 7 stops -$1,833, 3 targets +$622 |
Monday 11-16 alone: baskets -$6,443 (market -2.1%), megacap 15 -$799 (Thursday's calls held because Friday's decision
crashed: a process loss, fixed that day), trading books -$3. Reading: the model books are UP since they started; the
red is (a) baskets that simply hold the market, (b) the two alert books. Classification of every loss so far:
**process** (stale megacap 15 positions -$727 to -$799; stale / pre-halt prints RAYA, FISN, OMH; all fixed in code),
**market** (every basket dollar), **rule as tested** (every alert stop: the fade's test has 25% of trades stopped at
-20%, the long book's test wins 14%), **not real** (9 paper shorts with no shares: +$2,046 never earnable; fixed by the
borrow rule). No loss is unexplained.
What the same evidence does NOT support (checked that night, descriptive):
- "Skip morning shorts": 16 of the 22 live shorts were before 11:00 and lost -$2,600, and on the forward record
  mornings are -2.3% (n 39) against +3.0% later (n 15). But under today's rule set (no news, beta < 3, no repeat;
  the repeat rule approximated from the tested events) the tested mornings made +2.68% / +1.32% per trade (training n 376 / held-out n 136; the first half-hour is the
  weak part, +1.85% / +0.28%): skipping them would give up about half the tested profit. Not a rule.
- Borrowable names only (the rule since 11-16): forward record, rule set and shares at IBKR, n 22: -2.7% per trade,
  36% won; without the borrow condition n 39: +1.4%. Too few to decide; history says Alpaca-shortable names lost
  -0.55% (finding 9). This is the open question that decides the fade, and only more forward trades answer it.
- The forward record's 25 stops would have averaged -19% without a stop as well (worst -193%): the stop is not the
  leak. Its 56 other trades averaged +10.9% before costs.
- Model long calls split by time (training only): +0.29% per call = overnight +0.16% and next-day session +0.14%;
  an overnight-only book has the same Sharpe (1.03 against 1.09) at half the return. Not a rule.
- The alerts carry three fields (ticker, type, price) and all three are used; nothing in a message is left unread.

**MPU short 2026-11-17 (first short after the switch went back on; owner: "why didnt we just sell the short when it
reached 10:04?"; owner's pasted note asks for a stop audit).** Official bars: open $1.29, $2.03 at 10:01, alert
10:02:02 (gaining momentum, no news, beta 0, 350,000 shares at IBKR, quarter size $1,250). Short $1.54 at 10:03. 10:04:
low $1.31 (+14.9% open profit), close $1.33; back at the entry by 10:10-10:15; $1.78 at 10:21, $1.81 at 10:37 and
10:51; **$2.05 at 10:54, above the $1.848 stop**; the live book covered on the 60-minute timer at 11:03 near $1.70
and texted -12.1% (-$152). The note is right: the stop was crossed nine minutes before the exit. Cause: the live
monitor sees the free feed only, which printed 12 one-minute bars in that hour (highest $1.80) while the official
tape traded 433,000 shares in the 10:54 minute. The official result is the stop: about -21% (-$266). Classification:
rule loss (a stop) plus the known live-data gap: the first of 23 live shorts where the free feed missed an official
stop entirely (the 5 earlier official stops were all seen live; AMIX 11-10 and the FCUV long 11-13 saw theirs late).
Real-time official data is paid (section 7 proof standard); the official bars arrive free 15 minutes late. Since 2026-11-17 the listener reads them each minute and closes or corrects a short whose stop they crossed (section 2, row "stop"): MPU would have been corrected at 11:10 instead of at the nightly settle. On the
10:04 cover: the rule has no profit line by design; +15% target, half off at +3% / +5% and a 5% trail all made less
than holding over 3,113 fades (section 4), because the usual path is bounce then second leg down; MPU went one leg
down and all the way back. Its low missed the tested +15% line by one cent. The owner's "two stoppage lines" (cover once price
rises X% off its low while still under the entry) was pre-registered and run the same day at X 5 / 10 / 15%: FAIL
(section 4): it rescues 5-25% of losers and ends 26-88% of winners early, a net cost of 0.5 to 1.2 points a trade. The note's
"record says shorts were OFF" came from stale lines in this file (finding 20, the score table, section 7), corrected
the same day.

**"It still waits too long" (owner, 2026-11-17 afternoon; challenged with the live record and one test).** Longs: 15
live trades, 11 stopped after a median of ONE minute (longest 11), none reached its 30-minute exit: the long book does
not wait, it is stopped at once (entries, not holds). Shorts: 22 settled, 5 stops, 9 timer exits that won (+11.0%)
and 8 that lost (-6.4%): slow grinds against the short, the owner's case. A clock rule for exactly that case, "leave
a short that is losing at half time", was pre-registered and run once (section 4): shorts losing at 30 minutes made
-6.1% cut there and -5.5% held to the end; three in ten come back to a profit. A wash, slightly negative: not added.

**Exit timing and long entry timing, mapped once (owner, 2026-11-17 15:00: "once a stock reaches its lowest and keeps
rising for a certain amount of time ... and we still have made profit, get out"; "the longs ... entering at the most
optimal time"; `prereg_timing_maps_2026-11-17`).** Instead of one more threshold, every such moment on 2,345 tested
shorts: low 5 / 10 / 15 / 20 minutes old, price above it, trade in profit, by the profit on the table (12 cells). No
cell favours leaving: with 10% or more on the table holding adds +0.4 to +2.0 points, with under 5% it is the same
either way (-0.13% to +0.30%), and only 1% to 12% of such trades go on to the stop. For longs, a buy 0 / 5 / ... / 90
minutes after the alert held 30 minutes (27 cells with the price against the alert price): every cell loses after
costs, from -3.2% at the alert to -0.95% at 90 minutes; the fall fades out over about 90 minutes and then nothing
happens, so there is no bounce to buy; buying a 10% drop is the worst cell. Both questions are closed on this
channel's alerts: the short's hour is not the leak, and no entry time makes the long work.

**The short's entry price: rest an offer above the market? (`prereg_fade_limit_entry_2026-11-17`, FAIL.)** A sell
limit 2 / 5 / 10% above the first print for 3 or 5 minutes, market if not filled: +0.53% / +0.39% / +0.04% / -0.36%
per trade against +0.97%. Measured why: a third of the trades squeeze 5% in the first 3 minutes; on those the offer
is 5.3 points better (+2.50% against the champion's -2.79%, 44% of them stopped). The other two thirds fall at once,
and they are the fade's whole profit (+2.48%, 15% stopped); entered 3 minutes late they make -0.70%. The first print
is the right entry: which trades will squeeze is known only afterwards, and waiting to find out costs the best ones.

**The short's entry second (`prereg_fade_entry_seconds_2026-11-17`, FAIL; official prints of 2,470 alerts).** The rule
waits 10 to 70 seconds for the next minute bar. Entering at alert + 10 / 15 / 20 / 30 / 45 / 60 seconds instead:
+0.49% to +0.82% per trade against +0.97%, less in total at every second. Measured: (1) the price is flat on
average through the first minute after an alert (entry price against the bar open: mean within 0.2%, median 0.00%),
so no second is a better price; the fall begins after it; (2) an entry inside the minute trades 85 of the 121
halted alerts and loses 5.5% on each; (3) it has no print to hit on the 151 thinnest fades, which make +3.44% under
the bar rule. Moving the live entry to the bar on 2026-11-09 was right. Being minutes late costs; seconds do not.

**After the close: the alert day is a top on every clock (`prereg_alert_after_day_2026-11-17`; 2,319 alert days).**
From the alert day's close: overnight -1.1%, one day -2.5%, two days -3.9%, five days -6.3% on average; the median
stock is 12.6% lower after five days and only 30% are higher. Worse with company news (-7.7% in five days) and after
a weak close (-7.9%); a strong close is flat for a day (+0.3%) and then falls too. No long cell is positive after
costs (0 of 25), although delisted names are missing, which flatters a long. With the 27 entry times inside the hour
this closes the long side on this channel from one minute to five days. The short side of the same drift is large
and unproven: it needs borrowed shares every night at unknown fees and has no loss cap over a gap (section 0); it
could only be measured forward, with the IBKR shares and fee recorded at each close.

**A cell from that map, tested on new data, vanished (`prereg_day2_open_short_2026-11-17`, FAIL).** The map's
largest short-side cell needed no overnight shares: short the open the day after an alert day that closed weak,
cover at the close (+1.05% net on 943 training days). On the held-out period, never read before: -0.28% (322 days,
lower bound -3.0%), while the opposite group, strong closes, made +4.0%. One cell of 25 picked for its size is
mostly selection; nothing from a map is a rule until a second sample agrees. The worst day-after short lost 213%
without a stop.

**The multi-day drift on stocks that can really be borrowed: a forward test, clock running
(`prereg_after_day_borrow_forward_2026-11-17`).** The runner has stored the IBKR stock-loan list (shares and annual
fee, about 19,000 symbols) every 15 minutes since 2026-11-04 (`data/borrow/`, about 11 MB a day). So from that date
the missing ingredient of the after-day finding exists. Frozen rule: on alert days whose stock is lendable at the
close for a $2,500 position, short the close and cover after one or five sessions (earlier if the shares disappear),
after the spread and the highest fee seen. Scored ONCE at 300 borrowable alert days, roughly three to four months
away; until then `scripts/after_day_borrow_study.py --count` shows only how many days are eligible. A PASS would
still need a loss cap and the owner's word (section 0).

**The long texts were late, and the paper fills were back-dated (owner, 2026-11-18: "challenge our work ... the
signals come in late for the actual buys"; live audit of all 18 longs with an entry, 11-10 to 11-17, on official bars
and prints).** Classification: **process** (our own design), on top of a rule that is late by construction.
Three layers: (1) the channel alert fires a median 27 minutes and +19% after the stock's last 9/21 cross (15 trades
with a warmed EMA); (2) the book then waits a median 3.8 minutes for its pullback candle; (3) the book learned of a
fill only when the FILL bar had closed (closed 1-minute bars of the free feed, read every 30 s), so the text went out
64 to 88 s (median 79 s) after the booked price. In those seconds the price moved a median 2.4% (mean 3.7%) against
a median stop distance of 2.8%: more than the whole stop in 8 of 14; the stop level had already traded in 6, the
target was already passed in 2 (GEVO, GP). ADXN: booked 5.30, stopped on a wick inside the entry minute (-3%), 5.52
when the text arrived, +22% within 30 minutes. ZDGE: booked 3.28 and a -2.8% stop loss; the first official print a
minute later was 2.89. So the long record is the rule's result at prices our own signal could not have traded, and a
person reading the texts could not follow it. Shorts: the live entry is a median 13 s after the rule's bar; live
price 1.2% worse than booked on 10 trades (not distinguishable from zero); not the problem.
Fix, owner's choice (option 1, then option 2 once this is validated): (a) `sgt.fast.long_armed`: the order is texted
the moment the setup's last candle is seen closed, BEFORE the fill ("LONG SETUP ARMED": buy-stop level, or buy at
market now with the last price; stop, target, how long the order lives), once per setup, at most two per alert, not
for an order that cannot beat costs; (b) `sgt.fast.text_px`: every night the official price at the moment each text
went out is stored beside the booked fill, with what buying THERE made (`text_px`, `text_how`, `text_gross`; armed
texts: `long_armed.px`), and the digest shows it. The book's own fills and totals are unchanged. Option 2 (bars
built in-process from the live trade stream, so a candle's close is known within about a second) follows after a
few sessions show the armed text arriving before the entry.
Not fixed by this: every long setup still loses in its test; an on-time text makes the rule followable, not
profitable. The trader's actual claim (buy the 9/21 cross while volume is already there) is under its own test on
official prints (`prereg_ema_instant_volume_2026-11-18`).

**Root cause of the three longs of 2026-11-18 (CUE, SVRE, CLRO; watched, not traded) and two gaps in our own
pipeline (owner: "was it because the alerts came in too late? ... find gaps in the implementation").** Rebuilt
minute by minute on the official tape and on the free feed the book reads.
- CUE (**rule as tested**): the spike topped at 4.00 in the 09:33 candle; the channel's alert was posted 09:34:08,
  after the top. The rule bought the first red candle's close at 3.61 (09:36), the stock fell to 3.27 by 09:42 and
  only then ran to 4.04 (10:04). The red-candle exit sold at 3.54 (-3.2%); holding to the stop would have lost 7.6%.
- SVRE (**rule as tested**): alert 09:51:14, top 1.15 at 09:54, -24% by 10:17. Bought 1.055, stopped in the same
  minute (-3.6%); the stop saved 18 points. The short was the right side and was blocked (no shares to borrow).
- CLRO (**process: our data feed**): the red candle's low was 7.16 on the free feed and 7.13 on the real tape. The
  stop at 7.16 was touched to the cent and the long was out at -1.7%; on the real candle it is not stopped and the
  target fills in the same minute (+2.0%).
First reading given to the owner that morning ("all three ran 9 to 12% after the entry, the direction was right")
was wrong for CUE and SVRE: it read the 30-minute high and ignored the path. Path first, then the extremes.
**Gap 1, the alert reaches us late.** Over all 362 alerts received (11-04 to 11-18) the row is written a median
27.7 s after the channel's posting time (mean 29.3, 90th percentile 52.9; within 5 s: 9%). On 11-18 every alert
landed at second :32 of its minute: the Telegram library re-asks the channel on the server's timer (about 60 s),
the channel is not pushed to us. It delays every text and the short's entry (live entry a median 13 s after the
tested bar's open); it did not decide the three longs (entries two minutes after the alert). Owner 2026-11-18:
leave it for now.
**Gap 2, the free feed is not the tape.** It carried 2.0%, 0.2% and 0.5% of the volume of those three stocks; 6
to 22 of about 70 minutes had no bar at all. On the 18 earlier live longs against the same setup found on the
official tape: same entry minute in 12, the live stop tighter than the real candle's low in 12 (median 0.5%),
and on the 14 scored both ways the live book made -3.54% per trade against -1.47% for the rule on real candles.
The live long book has not been trading the tested rule; it has been trading it on different candles. Owner
2026-11-18: connect Schwab market data, keep the free feed as the fallback. `sgt.intraday.schwab_md` reads Schwab's
1-minute candles (read-only, not wired into a book); `scripts/schwab_md_check.py` must first show on a real login
that they match the official tape and arrive in real time. Option 2 (bars in real time) is only worth building on
a full-tape source: faster bars from the free feed would be faster wrong candles.

**Rule survey, 2026-11-18 (owner: "survey the rules ... not one general rule for every scenario").** Two frozen
plans, each run once.
(1) `prereg_long_feed_replay_2026-11-18`: the long book replayed on the free feed's past candles for all 2,464
tested alerts. The free feed (a median 0.55% of the volume) does NOT make the long book worse: found on its
candles the book makes -1.34% per trade (1,463 trades), on real candles -2.07% (2,139); the 665 alerts the feed
shows no setup for are the rule's worst (-2.95%, 9% wins). The morning's claim from 14 live trades is withdrawn.
Its stops are 0.6% too tight (median). A lower stop passed the frozen gate on training and, for the registered
2.0% pad, on the held-out events too (822 events), but per trade it is worth +0.07 to +0.15 points there and not
distinguishable from zero: the gate (size x net) rewards the smaller size of a wider stop. Not adopted as a
repair; the same saving is a smaller size. Held-out confirms the main finding: free feed -1.84% per trade against
-2.32% on real candles. 45% of the rule's trades are stopped on the bar they fill; for "buy the open after the first red
candle" 68%, with 13% winners.
(2) `prereg_scenario_map_2026-11-18`: every alert placed by how far the stock had already run from its low
(RUN) and whether it was still at its high. The median alert arrives 45% above the day's low. The plain long
loses in all six scenarios in both samples (-0.1% to -4.5% in 30 minutes after costs). The fade's edge sits in
the middle and late bands; the one scenario that passed on training (late, off the high: +2.98%) did not confirm
on the held-out events (+1.51% against +1.04% for the rest). An entry that waits for the red candle's low to hold
one or two candles FAILED (-1.23% / -1.46% per trade against -1.17% for today's entry at equal size).
What it means: there is no better way into the same alert for a long. Lead for the short book, to be judged on
new alerts only: fades on EARLY alerts (RUN under about 34%) lost in both samples (-0.2% to -1.2%).

## 6. How the system gets smarter
- Every idea becomes a pre-registered rule, tested once on train with a champion guard; held-out is secondary.
- Ideas that pass dev, and ideas that can only be tested forward, run as **shadow challengers** on every live alert
  (entry delay, holds, exits, brain sizing, time-of-day sizing, confidence sizing, price sizing, calm skip, vol-up sizing; StockTwits buzz recorded).
- **Drift guard** (2026-11-12): the live monitor compares each live book with its tested expectancy every night; an alarm halves that book's size for new trades and texts the owner (section 2). It also judges the fade's forward record (every alert), which is ~10x more trades than the live book and would have caught the November break on 11-10.
- The **learning scoreboard** (`scripts/learning_report.py`, nightly 22:05 ET, `reports/learning/scoreboard.md`, digest
  line) scores each challenger against the live rule on forward trades and marks it READY at n >= 300.
- A READY challenger that beats the live rule is proposed to the owner with its numbers; with an OK it is written as
  a prereg amendment and becomes the live rule. A worse one is retired. Nothing is promoted automatically.
- Live data feeds the next questions: borrow stamps, StockTwits buzz, X buzz, Reddit buzz, signals at entry, news and filings per trade.
- **Daily ops texts**: 09:20 ET pre-market health check (silent unless the listener, market data, database or disk
  would cost the day, or a book is switched off / the system is paused: since 2026-11-17 that is said before every
  open it stays so, after the silent morning of 11-16), midnight day summary, nightly digest, Friday 23:15 weekly review.
- **Replay lab** (2026-11-17, owner: "see it visually very clearly ... what's going wrong"; `sgt.lab`,
  `scripts/lab.py`): any ticker and day (or N random past alerts, or today's tape up to 15 minutes ago) replayed with
  the books' OWN rule code and drawn on one page: the alert, the short (entry, 20% stop, exit), every long setup with
  its stop and target, the owner's bull flag finder, break of structure / change of character, our own spikes, halts,
  and a minute-by-minute list. A replay of the first k bars shows nothing the live system could not have known by then
  (tested). It is for finding rules that do not do what we think; an idea seen on a chart still goes through a
  pre-registered test before it changes a rule (finding 6). On the site (2026-11-17, owner decision 5): the link
  "Lab" next to the tabs opens a second page under the same secret address with every alerted stock of the last
  two sessions replayed and what the live books did with it; rebuilt every 10 minutes in market hours and after
  the nightly settle (job `lab`). Read-only: the page is one static file; nothing on it can change anything.
- **Channel health** (nightly digest): alerts today vs the usual day; a quiet channel or a listener that stopped
  receiving it is flagged the same night, because the edge is the channel's selection (finding 11).

## 6b. The daily per-stock model (Model Tape, `scripts/model_tape.py`)
At 15:50 ET each day it predicts the next close-to-close move per stock and goes long / short / flat (walk-forward,
each month predicted by a model trained on earlier months). Out of sample 2018-2026 on 20 large caps it beats simply
holding on NONE of them: best GOOGL Sharpe 0.74 vs hold 0.76 (+12%/yr vs +24%/yr); NVDA +10%/yr vs +55%/yr; six
names lose outright; hit rates 44-55%. AAOI (the trader's pick, `prereg_aaoi_daily_2026-11-06`): -6.7%/yr vs hold
+54%/yr, FAIL.
As a TIMING OVERLAY (hold, but step aside on its short days; `prereg_megacap_overlay_2026-11-06`) it raises Sharpe on
14 of 21 names and cuts drawdowns on all six development names (NVDA 1.11 -> 1.23), but only 7 of 12 unseen names:
a nightly forward shadow decides at 300 symbol-days. The live forward test on the original six (since 2026-10-30) took 11 calls in
5 sessions, 10 profitable (+1.6% average); far too few to judge. Kept as a forward test, not a money strategy.
Review 2026-11-13 (10 sessions): 17 calls, 14 profitable, ledger +4.44% while holding the six made about 0%. Its own
2018-2026 record has a 10-day stretch that good 3.9% of the time and 14 of 17 winners 2.4% of the time: luck until
proven otherwise. In that record longs earn +0.33% per call and SHORTS +0.01% (n 1,448): the short calls carry no
tested edge, yet most of the recent gain came from them. No look-ahead (every decision was written the same evening).
Do not size up; the fixed decision points stand. The digest now shows each model set next to holding the same names.
Uses of the model compared on its 2018-2026 record, all 21 names (2026-11-13, already-seen data, descriptive): full
long/short +4.4%/yr (Sharpe 0.59, max drawdown -11%); LONG CALLS ONLY +6.4%/yr (Sharpe 0.92, -6%; positive in 8 of 9
years; long calls average +0.23% against +0.12% for an average day; 18 of 21 names positive); short calls positive on
only 9 of 21 names; overlay +32.3%/yr (Sharpe 1.29, -27%) against simply holding +34.8%/yr (Sharpe 1.26, -31%).
Holding is flattered: the names were picked knowing they became giants. Reading: the model's value is in its long
calls and in stepping aside, never in its shorts. Per stock and side: `config/models/call_stats.json`, shown on the
site's Overview tab as the tested record of each call (the honest replacement for a "confidence" number).
**Confidence (fade):** the stored 38-99 score is 100 x the chance that the AVERAGE of trades like this is profitable,
not the chance a trade wins (XLAB 11-12 scored 99 and lost 21%). Trades like those won 53-65% of the time. The site
now labels it "group score" and says so.
**Long calls only (forward test from 2026-11-16, owner's go 2026-11-13;** `prereg_megacap_long_forward_2026-11-13`**):**
a third model book, `megacap_long`, takes only the +1 calls of both sets (21 names), flat otherwise; no new model.
Decided on its equal-weight ledger (`sgt.megacap.longonly`): stop below -5%, continue on 2027-04-30 only if above 0.
The stop is enforced in code since 2026-11-15 (audit: it was only printed before): below it every target is flat,
what is held is closed at that day's close, and every model long card reads NO-GO.
The digest shows the ledger next to the long-and-short models and holding. Paper only.

**The money score (owner 2026-11-14: "the confidence score must actually work based on how much money we think we
are going to potentially make or lose"; `sgt.ops.score`):** every trade card and Notes page shows expected dollars,
the swing of a normal outcome and GO / NO-GO. Two pre-registered tests looked for something that ranks one call above
another, and both FAILED (section 4): the size of the forecast against today's risk
(`prereg_money_score_2026-11-14`: the forecast runs about 9 times too big, calibration slope 0.11, and a bigger one
earns no more) and the stock's own record so far (`prereg_record_score_2026-11-14`). What is left, and what the score
is: the tested average of the KIND of trade, on the dollars in the position.

| kind | tested average per trade | verdict |
|---|---|---|
| model long call | +0.25% (3,448 calls, 53% won) | GO |
| model short call | -0.08% (3,405 calls, 50% won) | NO-GO |
| alert long (A2) | -2.09% (2,242 trades, 14% won) | NO-GO by the test; ON again on paper since 11-16 (owner) |
| alert fade | +1.70% tested; the forward record while its guard is in alarm (-1.8% on 11-14) | NO-GO while in alarm; ON again on paper since 11-16 (owner), quarter size |
| ETF and overlay holdings | none: a basket held for comparison | yardstick |

It cannot rank two trades of the same kind, so size inside a kind stays an equal risk budget. Worked example, AMD
long of 2026-11-12 ($13,400): expected about +$34 by the score (+$98 by AMD's own long record, which the second test
says not to trust), normal swing +/-$990, result -$1,173: one call is noise, the edge is the average of hundreds.
Per $100k the long-calls book has been expected to make about +$45 a day, +$230 a week, with 54% of weeks green and
a normal week swinging about +/-$2,800 at the live size.

*(Moved here from section 4 on 2026-11-17, which is kept for alert trades: the daily model's rejected rules are the money score `prereg_money_score_2026-11-14` (high forecast +0.24% vs rest +0.27%, p 0.57), the record score `prereg_record_score_2026-11-14` (p 0.29), earnings holds and crowded days `prereg_long_rules_2026-11-15` (p 0.73 / 0.98), the market filter, the fresh names and the market timer below; none passed.)*

**Earnings and crowded days (`prereg_long_rules_2026-11-15`, both FAIL, nothing changed):** the two risk fixes named
in the 11-13 review were tested on the long calls. (E) Skipping a long call held through the stock's own earnings: the
47 such calls made +0.78% on average (62% won) against +0.25% for the rest, so skipping costs money; each swings
about 7.5% against 3.2% for an ordinary call (worst: NFLX 2022-01-20 -21.8%). (V) Cutting crowded days (calls that
move together) back to the typical day's risk: those days are where the money was made (Sharpe 0.57 to 0.32 at the
same money). So the book takes a long call into earnings like any other and has no theme cap; size is the only risk
lever, and the owner set it on 11-14. Honest note from the same run: by year the long-calls book made, in hundredths
of a percent a day, +16.0 (2020), +7.0, -1.8 (2022), +6.6, +3.5, +4.2 (2025) and -13.5 in January to June 2026: the
most recent half-year of the record is its worst, and July to October 2026 has never been opened.

**Audit of 2026-11-15 (independent code review and red team of the 11-14 build; no blocking finding):** fixed: a
trade card's dollars left out the book's cost on a resize and charged costs on the wrong day's value (cards read
slightly better than the book; they now equal the book's own change for a one-stock book); the score of a model call
now says it is per day held and carries its caveat (backtest on stocks chosen with hindsight, not yet proven live,
last half-year negative) with the live ledger next to it once there is one; the nightly digest now reads every job
log (the 15-name set's 11-13 crash was not in NEEDS YOU) and a repeating failure is one line; one bad number can no
longer stop the page from building. Confirmed sound: no look-ahead in either score study, held-out never touched,
numbers reconcile with their result files, the page cannot be injected through alert text. Monday's long-calls order
path was dry-run on the runner against the 11-12 decisions (AAOI 7%, AMD 11%, JPM 25%, proposed once).

**Cards against books, checked on the runner after the deploy (2026-11-15):** the "equal the book" claim above held
only where a book re-orders every session. Replaying each book from daily closes reproduced every stored value, and
the cards were below their books by $38 to $57 on the three ETF books, $48 on the overlay and $3 on the six-name set.
Two causes. (1) The book holds a constant weight on each day's value (`book.py`: "weights are held constant between
fills"); a card priced a multi-session hold from fill to fill, as shares held. Neither is wrong, but they are not the
same number, and the texts quote the book. (2) Three NVDA orders in one session (out, short, smaller short) are one
change of weight to the book and were three paid changes to the card. Fix: the book now stores what each held symbol
made each session (`book_legs`, written with the mark it explains), a card adds up those rows, and a session's orders
for one symbol count once. History is replayed once from closes (`sms_control.py booklegs`), refused for any session
more than $1 from the stored value. Dry run on a copy of the runner's book: 105 rows, none refused; four books to the
cent, the other two $0.10 and $0.17 off (an Apple dividend restated the earlier adjusted closes). A stretch with a
session the book has no row for keeps the older estimate. Known and left: the book gives a held basket a free daily
rebalance, worth about 0.04% to 0.06% of a 60/40-style book over these nine sessions, in the book's favour this time.

**Headline and measurement (owner 2026-11-14, interview):** the nightly, midnight and weekly totals count TRADING
books only (the long-calls book and the alert books). The two long-and-short model tests and the overlay are
MEASUREMENT: they keep running untouched for their evidence, their short calls are labelled NO-GO, and their dollars
are shown on a "not in the total" line with the ETF yardsticks. The long-calls paper book keeps its size (up to 25%
per stock): the owner chose it over 12.5% knowing the numbers (same expected dollars, about +$45 a day; normal day
+/-$1,240 against +/-$860; worst historical drop -22% against -14%; the deciding equal-weight ledger -5%).


**Live estimates are after estimated costs (2026-11-16, `sgt.fast.est_costs`).** Before the 20:40 ET settle a trade is
known only by the listener's prices, and every text, card, page total and the midnight summary showed that estimate
BEFORE costs, so a day read better live than it settled (11-13: -$100 live, lower official). One pure helper now applies
the official cost model (gapper round-trip spread at the entry time and price, plus the 0.5% locate on shorts) to the
live entry; every place that quotes an estimate uses it: the FADE OUT and LONG OUT texts and their day total, the
digest's long line, the midnight summary, the cards and their dollars, the Notes page (each alert trade carries
`est_cost`) and the AI review's title line. Size of it: a $3 long costs about 0.7% midday and 1.25% in the first
hour; a $3 short 1.2% to 1.75%. A flat trade is a loss, and a target closer than the cost is a loss when hit (a
4-cent target on a $3.82 stock, as VTSI 11-13, is +1.0%: against 1.25% in the first hour it loses, midday it keeps 0.3%). The official result still replaces the estimate at the settle
(official prices, same model). Mutation-checked: 20 of 20 reverted lines fail a test.

**Model books review, 2026-11-16 (owner: "we lost most of the alpha model trades performed today").** The positions
on the site were megacap 15's calls of Thursday 11-12, not today's: Friday's 15:50 decision crashed (one name had no
intraday bars; fixed in 432c7fb), no order was written, and the book kept one-day calls for two extra sessions.
Friday's decision (rebuilt from closes) had AMD, JPM, TSLA and ORCL flat. Monday to 15:40 ET, against Friday's close:
AMD long 13.1% -5.9% (-$785), AAOI long 7.0% -9.5% (-$677), JPM long 25% -2.7% (-$681), TSLA short 21.4% +$743,
ORCL short 13.6% +$747: about -$653 (official at the settle). The market: SPY -2.1%, QQQ -2.8%, SMH -5.2%, NVDA
-4.6% the day before its earnings; no news item dated 11-16 was found for a cause. Faults: (1) ops: a failed decision
left positions on; fixed 2026-11-16: with no valid live decision in the 15:50 slot a model book closes what it holds
(`sms_control.flat_without_decision`; megacap, megacap 15 and the long-calls book; the overlay basket is untouched);
with that rule Monday would have been $0, not -$653. (2) model: AAOI was the model's own repeated long (pred +2.4%
on a stock that moves 9% a day; -5.6% Friday, -9.5% Monday) and it is called long again for Tuesday (6.1%, also the
long-calls book's first trade). Nothing tested ranks one call above another (prereg_money_score, prereg_record_score,
prereg_long_rules: all FAIL), so no rule removes it. The one idea left, a market filter on new long calls, was
pre-registered and run once the same evening (prereg_long_market_filter_2026-11-16): **FAIL, in the opposite
direction.** No new long call while SPY is below its 200-day average would have cut the long book's 2020 to mid-2026
total from +82.6% to +2.2% (Sharpe 0.57 to 0.09, largest drawdown -22.3% to -30.3%; p 0.96). The 1,139 long calls made
below the trend earned +0.64% each (56% won); the 2,309 made above it +0.06% (52%). The model's long calls are
dip-buying that paid in falling markets and about broke even in rising ones: 2022 had 203 below-trend sessions and
the book made money on them; January to June 2026, its worst stretch, had 12. Idea closed (no other average, index or
threshold); held-out not opened. Do not re-propose a trend or "risk-off" filter for the long calls.

**The long calls on 80 large caps the model had never seen (`prereg_fresh_names_2026-11-17`): FAIL.** The +0.25% per
call was only ever measured on 21 names picked knowing they did well. Test: the same frozen model on the 80 most
traded US stocks of 2019 that it had not seen (chosen on 2019 volume, so it includes later losers: BA, INTC, ROKU,
PYPL, SNAP), 2020 to June 2026, 13,542 long calls. Both of two conditions were required. (1) The equal-weight
long-calls book makes money: +0.017% a day, lower bound just above zero (p 0.019): holds, barely. (2) A long call
beats the average of all 80 stocks that same day: +0.036%, p 0.069: fails. What it means: a long call made +0.15%
after cost against +0.06% for an ordinary day, in every year, so the idea is not empty, but it is 60 to 75% of the
21-name number, and what the model adds is mostly WHEN (it buys after falls, when everything tends to bounce), not
WHICH stock. Sized like the live long-calls book it made +53% with a -39% drawdown where simply holding the same
stocks made +123% with -36%. So: the long-calls book is a low-exposure dip-buying timer with a small positive tested
average, not a stock picker and not a replacement for holding. Its score on the site keeps +0.25% with the label
"not confirmed on unseen stocks". Do not widen the book to more names on this evidence; do not re-run or vary this
test on these names. The held-out months stay closed.

**The long calls as a market timer (`prereg_dip_timer_2026-11-17`, owner decision 2A): FAIL.** If the model knows
WHEN and not WHICH, hold the market itself on the days it calls many longs (the busiest fifth of days, cut known at
the time). 352 such days, 2020 to June 2026: SPY the next day +0.157% against +0.039% after other days; difference
+0.118%, p 0.067 (needed < 0.05); QQQ the same picture. All of it is 2020 (+0.73% a day); 2022 and 2023 were
negative. And a rule with no model, "the days SPY fell most", made +0.196% the next day; the model's signal days that
were NOT such falls made +0.063%, an ordinary day. So the model's timing is "the market fell today", nothing more,
and as a book the timer made +60% where holding SPY made +152%. Closed: do not propose the model's breadth, or a
buy-after-a-down-day index rule, from this evidence. With the fresh-names FAIL this settles what the daily model is:
a small, fading dip-buying tilt on large caps; not a stock picker, not a market timer, not better than holding.

**A scanner on the alphas: the signal list tested first (owner 2026-11-18, option A;**
`prereg_scanner_signals_2026-11-18`**).** Our own channel-style alerts (volume spike, new high / low of day, burst,
9/21 cross on volume; up and down) on the 20 liquid alphas, 2016-2024 minute candles, 68,474 signals: after each
signal the stock moves -0.02% to +0.05% in the next 15 minutes to the close, against a 0.04% cost. No cell passes
following or fading; held-out not opened. Fourth fast-signal family to find nothing on these stocks. The free
feed is good enough there (candles within 0.05% of the tape on 94-99% of minutes), the data is not the limit: the
signals carry no information. The scanner is not built on price-and-volume events.
**Model scorecard, 2026-11-18 (owner: "the models are getting better", asked for a plain score per model;
descriptive, already-seen record).** Grade = do the model's LONG calls beat an ordinary day of that stock
(2018-2026 out of sample, then the last 12 months): A GOOGL (+0.48% a call), AMZN (+0.36%); B AMD, WMT, AVGO;
C MSFT, NFLX, JPM, NVDA, MA, AAPL, AAOI, V, TSLA, UNH, META; D XOM, RDDT, LLY, COST, ORCL. All 21: long calls
+0.27% against +0.12% for an ordinary day over 8 years, but -0.03% over the last 12 months (556 calls); 11 models
worse lately, 7 better. Live (13 sessions for six names, 6 for fifteen): 46 calls, 30 winners, mostly NVDA (6 of
6) and SHORT calls (20 calls, +0.90% each) that have no edge in the long record (4,551 calls, -0.03%). The live
run does not show the models getting better; it is a short lucky stretch until it lasts. None beats holding.

**The trader's trend matrix as the long book's exit (SKYA, 2026-11-18;** `prereg_trend_matrix_exit_2026-11-18`**).**
SKYA: bought 3.33, the 30-minute timer sold 3.40, the stock closed 4.13 (high 4.39). The trader: stay in while the
1-minute column of his matrix (MACD 12/31/10, EMA 34, RSI 14, Bollinger middle, Supertrend 10/3, PVT 15) is all
green; MACD red = start leaving. Tested from the book's own entries on 2,139 trades: MACD exit -2.31% per trade,
any-of-six exit -2.20%, the book's exits -2.07%. The median trade lasts ONE minute under either: two in three are
stopped before a trend exists, the rest see a red condition at once. Entering only when the column is all green
is worse (-2.15% against -1.77%). SKYA by the same code: MACD and PVT were red at the candle before the entry, so
the rule sells a minute later for +0.3%. The matrix describes a trend once it is under way; it does not pick the
one alert in six that has legs. Kept as context for a card, never as the exit. Fifth "stay in the runner" idea to
lose (MACD hold, swing-low trail, half at target, runner exits, this).
**Earnings follow-through on mid-size stocks (**`prereg_earnings_midcap_2026-11-18`**; scanner input, owner's
pick).** Large caps were tested 2026-11-03 and failed on held-out. The same single rule (a move of 5% or more on
the earnings day, enter at that close in its direction, five sessions, hedged) on 683 mid-size stocks, 2017-2023:
6,522 events, -0.28% per event after a 0.30% cost (+0.02% before it); both legs negative; held-out not opened.
Not a scanner input.

**The trend matrix as the trader uses it (**`prereg_trend_book_2026-11-18`**; owner 2026-11-18: "take the
trader's advice to heart").** His words read again: in while the 1-minute column is all green, out when MACD GOES
red, in again when it is green again; a rule for a stock in play all session, not one exit on our entry. On every
alerted stock-day of the training set (2,464): 16,643 round trips, 7 a day, 11 minutes each; -0.21% a round trip
before costs, -1.15% after; 24% winners; the best 1% average +62%. The first entry after the alert loses (-1.58%
before costs); all later ones together are flat. Better on stocks above $10 (+0.29% before costs). So the rule
does what he says, it stays with the rare runner, and applied to everything it does not pay: his edge is in which
alert and which moment, which is what to learn from him. Decision (owner): the matrix is shown in the long texts
and cards and his rule is tracked on every live long.
**Scanner inputs, second round (owner 2026-11-18: "let's do both").** (1) Pre-market gaps by kind of news
(`prereg_gap_news_2026-11-18`): 6,311 gaps of 10% or more, 2024-10 to 2026-03, classed from company headlines
before the open. A gapper falls 1.0% from its open on average; buying it loses 2% after costs whatever the news.
Earnings gaps hold (0.0%), deal and contract gaps give back 4.1% by the close, offerings 3.5%; no cell passes long
or short (best: short a deal gap to the close, +2.54%, lower bound -1.23% over 28 tests). Held-out not opened.
Leads only: deal and offering gaps fade; no-news gaps of 20 to 50% rose. (2) The stock-loan list as a scanner
(`prereg_borrow_scan_forward_2026-11-18`): shares drying up or the fee doubling, scored both ways over 1 and 5
sessions; forward only, decided once at 300 events; until then only counts are read.

## 7. Open questions and decision points
- **Proof standard (owner 2026-11-09, fixed in advance):** a book is PROVEN only when its own LIVE trades reach
  n >= 300 and the 95% interval of mean net per trade (official, after real costs) is entirely above zero. No new
  spending (paid data, AI beyond ~$1/month) until a book is proven. Tracked nightly in the digest "Live vs tested".
- Forward, at 300 borrowable alert days: the multi-day short on lendable stocks (`prereg_after_day_borrow_forward_2026-11-17`).
- Forward n >= 300 (about 6-8 weeks): keep/drop the fade; IBKR-borrowable subset; news vs no-news live; brain and
  time-of-day sizing; StockTwits buzz hypothesis (`prereg_fade_stocktwits_2026-11-06`); X buzz hypothesis (`prereg_fade_x_2026-11-08`, A1 cheap collection); Reddit buzz hypothesis (`prereg_fade_reddit_2026-11-08`).
- Real money only on a strategy proven on unseen data and tradeable at a broker (borrow). Shorts also need section 0.
- **Fade back on: done 2026-11-16 by the owner's word, on paper** (section 0), at quarter size (drift guard) and only
  on names IBKR can lend. Still to bring before any real money: the forward record recovering (win rate back near 57%
  over 100+ new forward trades) AND the borrowable part alone being positive (forward n 22 so far: -2.7%).
- **The fade trades on paper since 2026-11-16; the long book is measure-only with "seen" texts since 2026-11-17** (owner). Neither has a
  rule that passed its test; still measured nightly: the fade forward record, every shadow challenger, the long
  shadow book. Real money only for a pre-registered rule that passes training and held-out.
- **Long side (owner's focus):** the owner's Clean Bull Flag Finder (prereg_bull_flag_finder_2026-11-13, run once);
  BoS / CHoCH with EMA 9 > 21 (owner: "nowhere to go but up") once the Pine source arrives.
- Broker path (`docs/research/broker_borrow_2026-11-06.md`): IBKR has no locate fee and no borrow fee on same-day
  shorts [VERIFY] but often no shares; locate brokers (TradeZero, Cobra, CenterPoint) can short them for a per-share
  fee. Break-even locate = 1.6% of price (e.g. $0.04/share at $2.50); two thirds of trades are under $5.
