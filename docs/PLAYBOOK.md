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
| size | 5% of book NAV per trade, max 15 trades/day, 8 open, -3% day loss stop | fade_rule.py |
| approval | not ARMed: enters at the tested time, text carries "NO <code> = exit now" (F5) | prereg_alert_fade F5 |
| confidence | 100 x P(trades like this are profitable on average): news x time of day, 38-99 | prereg_fade_news A2 |
| texts | trade card, news, SEC filings, StockTwits buzz, chart read, borrow, TradingView link | fade_texts.py |

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
8. **The stop is a trade-off, not a free fix.** No stop +2.31%/trade on train but with -78% worst cases; 20% stop
   +1.47% (exploration on train). About 25% of fades lose more than 15%. RAYA (2026-11-06) was stopped at the top of
   its squeeze and fell back below our entry 15 minutes later.
9. **Borrow is the real-money question.** Names Alpaca can short lost -0.55%/trade held-out; live stamps show the
   biggest winners (CRIS, AEHL) had zero IBKR shares. Decided at forward n >= 300 on the IBKR-borrowable subset.
10. **Chart signals carry no edge here.** EMA 9/21 cross (46,711 signals), RSI, MACD, PSAR, PVT, VWAP, the trader's full
   setup, 30+ signals in a learned model: about 0 before 0.7-1.4% round-trip costs (`ema_cross_check_2026-11-05`,
   `docs/research/signals_research_2026-11-05.md`). They are shown in texts as context, never as filters.
11. **The edge is the channel's selection, not the price pattern.** Spikes we detect ourselves (5% in 5 min, 5x
   volume, new high; 517 in training) fade only +0.11% before costs, -1.23% after; the 4% the channel also alerted
   earned +4.7% (`prereg_self_fade_2026-11-06`). The book depends on that channel staying as it is.
12. **Price matters, and it is not where you would guess.** By entry price (train / held-out): $1-2 +2.4% / +2.1% per
   trade (about two thirds of all profit), $2-5 +0.2% / +0.2%, $5-10 +0.9% / +1.2%, $10-50 +0.3% / +0.8%. A $0.02/share
   locate still leaves $1-2 fades at +1.4% / +1.2%. Descriptive (found after looking), so forward-only if used.
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
| fade our own price/volume spikes (no channel) | -1.23% (n 517) | sealed | prereg_self_fade_2026-11-06 |
| other strategies: trader rules, ORB stocks-in-play, crypto C1/C2/C2M, earnings, large-cap intraday | all FAIL | | docs/findings |

## 5. Loss reviews (each: facts, classification, lesson, test)
| trade | facts (official bars) | classification | lesson / action |
|---|---|---|---|
| RAYA 2026-11-06 -20% | alert during a LULD halt; live "entry" $1.425 was a stale print, real reopen $1.52; stop at the squeeze top, back below entry by 10:01 | execution bug + news trade + stop trade-off | stale-quote guard shipped; 3 exit/entry ideas tested, all lose |
| SERV 2026-11-06 -2.4% | +2.8% at 10:33, ground up, 60-min exit | rule loss, news trade | partial-profit idea tested: -0.50%/trade |
| STAK 2026-11-06 -2.3% | +4-8% for 40 min, rebound at 11:07 as the timer fired; rose to -9.6% after our exit | rule loss (timer saved a larger loss) | same |
Process: `docs/runbooks/loss_review.md`; facts: `scripts/loss_review.py SYMBOL DATE`.

## 6. How the system gets smarter
- Every idea becomes a pre-registered rule, tested once on train with a champion guard; held-out is secondary.
- Ideas that pass dev, and ideas that can only be tested forward, run as **shadow challengers** on every live alert
  (entry delay, holds, exits, brain sizing, time-of-day sizing, confidence sizing, price sizing; StockTwits buzz recorded).
- The **learning scoreboard** (`scripts/learning_report.py`, nightly 22:05 ET, `reports/learning/scoreboard.md`, digest
  line) scores each challenger against the live rule on forward trades and marks it READY at n >= 300.
- A READY challenger that beats the live rule is proposed to the owner with its numbers; with an OK it is written as
  a prereg amendment and becomes the live rule. A worse one is retired. Nothing is promoted automatically.
- Live data feeds the next questions: borrow stamps, StockTwits buzz, signals at entry, news and filings per trade.
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
- Forward n >= 300 (about 6-8 weeks): keep/drop the fade; IBKR-borrowable subset; news vs no-news live; brain and
  time-of-day sizing; StockTwits buzz hypothesis (`prereg_fade_stocktwits_2026-11-06`).
- Real money only on a strategy proven on unseen data and tradeable at a broker (borrow).
- Broker path (`docs/research/broker_borrow_2026-11-06.md`): IBKR has no locate fee and no borrow fee on same-day
  shorts [VERIFY] but often no shares; locate brokers (TradeZero, Cobra, CenterPoint) can short them for a per-share
  fee. Break-even locate = 1.6% of price (e.g. $0.04/share at $2.50); two thirds of trades are under $5.
