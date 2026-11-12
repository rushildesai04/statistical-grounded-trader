# SGT Playbook — the alert fade (living knowledge base)

Single source of truth for what the fade does, why, what we have learned, and how it gets smarter. Updated after every
study and every loss review (owner, 2026-11-06: "distill all of it ... keep making it more intelligent"). Numbers cite
the file that produced them; "train" = 2,349 fades 2025-01..2026-06, "held-out" = 764 fades 2026-07..10 never used to
build the rule.

## 1. What we trade and why it works
Short small caps ($1-50) right after a MetaTradings momentum alert. Mechanism: retail-attention buying sprees in small
caps are followed by falling prices (Barber, Huang, Odean, Schwarz, "Attention Induced Trading and Returns"); the alert
marks the spree. Held-out result: **+1.12% per trade net of costs**, win 57%, n 764 (`prereg_alert_fade_2026-11-01`).

## 2. The algorithm today (exactly what runs)
| step | rule | source |
|---|---|---|
| entry | first 1-min bar at or after alert + 10 s (live: latest IEX trade; a print older than 90 s after a >= 10% move in 5 min looks like a halt and is not a price; without such a move it is a thin-on-IEX stock and is used) | prereg_alert_fade; RAYA / SNT fixes 2026-11-06 |
| filters | alert type in the 10 cells, 09:30-15:44 ET, $1-50, first event per symbol-day | prereg_alert_fade |
| stop | 20% above entry (worst case inside a bar) | prereg_alert_fade (10/20/30/none explored on train) |
| exit | 60 minutes after entry, or the 16:00 close if earlier | prereg_alert_fade |
| beta (F7) | no short when the stock's beta >= 3 (owner 2026-11-10; past: >= 3 -2.14%/trade, < 1.5 +1.86%). The scanner followed the same rule on 11-10 and is paused since 11-11 (owner: longs first; it still records and scores spikes: digest line) Unknown beta still shorts: tested 2026-11-11, no-news unknown +1.03%/trade (n 367), not worse than beta < 3 (p 0.36) | fade.py, prereg_alert_fade F7; scanner.py, prereg_scanner_book A1 |
| size | 5% of book NAV per trade by default; text `SIZE 10000` sets dollars per trade ($500 to 25% of the book; raising needs a code). Runaway guards only: 40 open, 200% of the book short. No daily cap or loss stop (F6) | fade.py, owner 2026-11-09 |
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
10. **Chart signals carry no edge here.** EMA 9/21 cross (46,711 signals), RSI, MACD, PSAR, PVT, VWAP, the trader's full
   setup, 30+ signals in a learned model: about 0 before 0.7-1.4% round-trip costs (`ema_cross_check_2026-11-05`,
   `docs/research/signals_research_2026-11-05.md`). They are shown in texts as context, never as filters.
   On the alert long book the trader's five rules (9/24 cross, volume spike, MACD+, PSAR green, above VWAP) pick WORSE
   trades: all five true -3.13% vs -2.02% otherwise; each rule alone also worse (`prereg_trader_rules_long_2026-11-12`).
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
   high, which happens after the entry: with point-in-time data it vanished (`docs/research/architecture_audit_2026-11-09.md`).
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
   0.85% / 0.87% vs 0.90% / 0.89% modelled; net +1.18% / +1.33% at real spreads (`docs/research/real_spreads_2026-11-08.md`).
   The edge is not a cost illusion; borrow/locate is the open cost question.
   Resting limit exits do not beat the spread: fills save +0.58%, misses cost -0.59% (queue model, finding from
   `prereg_fade_passive_exit_2026-11-08`). Test fills with trade-level data and the queue, never 1-minute bar lows.
14. **Losing streaks do not predict the next fade; do not stop after losses.** After the day's last two known results
   were losses, the next fade earned +2.18% (training, n 205) / +2.53% (held-out) vs +0.86% otherwise; a daily breaker
   costs ~1.3% per session. Day halves are uncorrelated (`prereg_fade_dayflow_2026-11-08`).
13. **Sizing by a model's ranking may help** (dev +2.37%/session, held-out secondary +8.31%), but forward evidence so
   far is negative (n 34). In shadow until n >= 300.

## 4. Rules tested and rejected (do not re-test without a new reason)
| idea | train per trade (champion +0.97%) | held-out (champion +1.12%) | file |
|---|---|---|---|
| trailing 5% stop | -0.18% | -0.50% | prereg_fade_exits_2026-11-05 |
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
| LONG every alert (5% stop, +5% target, 15 min; also +3/+8%, 30 min) | -1.65% (51% stopped) | -1.72% | prereg_long_family_2026-11-09 |
| LONG calm alerts (the quintile that loses as a short) | -0.87% | -1.11% | same |
| LONG the reopen after a halt | -2.12% (63% stopped) | -3.52% | same |
| LONG the first red candle after the alert (next open / break of its high) | -1.19% / -2.44% | -1.27% / -2.36% | prereg_long_setups_2026-11-10 |
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
| fade: cover when a bull flag breaks out (buy-stop at the flag high) | +0.09% per fade vs plain | +0.05% (p 0.38; no-news 0.00%) | prereg_fade_flag_2026-11-10 |
| fade: skip when a bull flag completed in the 15 min before the alert | skipped -0.87% (p 0.17) | skipped +2.91% | same |
| stop for the day after two losses (breaker) | skipped trades earned +2.18% | +2.53% | prereg_fade_dayflow_2026-11-08 |
| skip when >= 3 fades in the prior 30 min (burst) | +0.07% skipped (n.s.) | +1.65% skipped | same |
| fade our own price/volume spikes (no channel) | -1.23% (n 517) | sealed | prereg_self_fade_2026-11-06 |
| other strategies: trader rules, ORB stocks-in-play, crypto C1/C2/C2M, earnings, large-cap intraday | all FAIL | | docs/findings |

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
Since 2026-11-11, Luna writes in this format and checks every idea (its own and the note's) against sections 3-4. The result is labelled "tested: lost", "untested" and so on. Luna re-reads a note once it is saved.

Process: `docs/runbooks/loss_review.md`; facts: `scripts/loss_review.py SYMBOL DATE`.
AI reviews: since 2026-11-10 the per-trade review, the nightly brain and the Notes tab include the long book (before, longs never reached them).
Long book official result (2026-11-11, owner: "real long scoring"): the live trade's OWN order (fill bar, buy-stop trigger, stop) replayed on SIP bars by the tested simulator, like the fade. Before, it was the shadow's SIP re-detection, which can be a different trade: KULR 11-10 lost live (IEX trigger $2.155, stopped -1.2%) but was recorded +1.63% (SIP trigger $2.115). Old rows are re-scored once. The long shadow stays as the rule's own measurement, so live vs tested = execution gap.

## 6. How the system gets smarter
- Every idea becomes a pre-registered rule, tested once on train with a champion guard; held-out is secondary.
- Ideas that pass dev, and ideas that can only be tested forward, run as **shadow challengers** on every live alert
  (entry delay, holds, exits, brain sizing, time-of-day sizing, confidence sizing, price sizing, calm skip, vol-up sizing; StockTwits buzz recorded).
- The **learning scoreboard** (`scripts/learning_report.py`, nightly 22:05 ET, `reports/learning/scoreboard.md`, digest
  line) scores each challenger against the live rule on forward trades and marks it READY at n >= 300.
- A READY challenger that beats the live rule is proposed to the owner with its numbers; with an OK it is written as
  a prereg amendment and becomes the live rule. A worse one is retired. Nothing is promoted automatically.
- Live data feeds the next questions: borrow stamps, StockTwits buzz, X buzz, Reddit buzz, signals at entry, news and filings per trade.
- **Daily ops texts**: 09:20 ET pre-market health check (silent unless the listener, market data, database or disk
  would cost the day), midnight day summary, nightly digest, Friday 23:15 weekly review.
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

## 7. Open questions and decision points
- **Proof standard (owner 2026-11-09, fixed in advance):** a book is PROVEN only when its own LIVE trades reach
  n >= 300 and the 95% interval of mean net per trade (official, after real costs) is entirely above zero. No new
  spending (paid data, AI beyond ~$1/month) until a book is proven. Tracked nightly in the digest "Live vs tested".
- Forward n >= 300 (about 6-8 weeks): keep/drop the fade; IBKR-borrowable subset; news vs no-news live; brain and
  time-of-day sizing; StockTwits buzz hypothesis (`prereg_fade_stocktwits_2026-11-06`); X buzz hypothesis (`prereg_fade_x_2026-11-08`, A1 cheap collection); Reddit buzz hypothesis (`prereg_fade_reddit_2026-11-08`).
- Real money only on a strategy proven on unseen data and tradeable at a broker (borrow).
- Broker path (`docs/research/broker_borrow_2026-11-06.md`): IBKR has no locate fee and no borrow fee on same-day
  shorts [VERIFY] but often no shares; locate brokers (TradeZero, Cobra, CenterPoint) can short them for a per-share
  fee. Break-even locate = 1.6% of price (e.g. $0.04/share at $2.50); two thirds of trades are under $5.
