# Day-trading signals: what traders use, what the evidence says, what we tested (2026-11-05)

Owner request (2026-11-05): research the signals day traders actually use on minute-to-hour horizons, put them on the
chart, feed them to the learning layer, and treat news as the most important input. This is the inventory and the
plan. Every number below cites the file that produced it.

## 1. The signals momentum day traders use
Sources: the trader's own walkthrough (2026-10-13, encoded in `src/sgt/decision/trader_rules.py`); Ross Cameron's
"five pillars" as summarized by public scanner scripts (price $1-20, up >= 10%, relative volume >= 2-5x, float
< 10-20M shares, a news catalyst; float rotation >= 1x); Zarattini, Barbon and Aziz (2024) for the opening range and
relative volume. Practitioner thresholds vary by source; none publish out-of-sample results.

| family | signal | what traders read from it | in our code |
|---|---|---|---|
| Trend | EMA 9 / 21 (trader: 9/24) cross and gap | momentum turning; "9 below 21 = world of pain" | `sgt.fast.signals`, `decision.features` |
| Trend | MACD 12/26/9 histogram | momentum fading when bars shrink | both |
| Trend | Parabolic SAR, SuperTrend, ADX | trend side and strength | `decision.features`; PSAR also in `fast.signals` |
| Overbought | RSI 14 | don't chase above 70; reversal when it drops back | both |
| Volume | relative volume (bar, cumulative, opening range) | "volume confirms"; stocks in play | both |
| Volume | PVT vs its 21-bar average | buying pressure building or thinning | both |
| Volume | float rotation (volume / float) | supply exhausted, squeeze potential | `decision.features` (EDGAR shares) |
| Level | VWAP (institutions' "wash" line) | above = buyers in control; reclaim / loss | both |
| Level | high of day, premarket high, opening range | breakout triggers | both |
| Volatility | ATR, Bollinger width | stop distance, sizing | both |
| Context | gap %, price, time of day (open / midday / power hour) | which setups work when | both |
| Catalyst | company news, earnings, FDA, offerings, 8-K | why it is moving; whether it lasts | `fast.news`, `decision.news_features`, `fast.filings` |
| Flow | level 2 / tape, halts | order-book pressure, momentum halts | not available (paid feeds) |

## 2. What the evidence says
- Published, positive, minute-level: the opening-range breakout on the day's top-20 relative-volume "stocks in play"
  (Zarattini, Barbon, Aziz 2024, SFI RP 24-98): Sharpe 2.81, 41.6%/yr 2016-2023, net of commissions only. A
  QuantConnect replication matched one year; practitioners report that slippage at the open shrinks it a lot. This
  is the one published price/volume day-trading rule with real evidence, so it is now under a faithful, sealed test
  (section 4).
- Retail attention: the most-bought small caps fall over the following days (Barber, Huang, Odean, Schwarz,
  "Attention Induced Trading and Returns"). This is the mechanism behind our fade, the one strategy that passed.
- Day trader outcomes: population studies (Brazil, Taiwan) find the large majority of individual day traders lose
  money and only a small fraction are persistently profitable. Individual track records are survivorship-selected.
- No peer-reviewed study found that EMA crosses, RSI, MACD or VWAP alone predict small-cap minute returns after
  spreads; trading-education sources assert it without out-of-sample tests.

## 3. What we measured (all pre-registered or recorded; small caps cost 0.7-1.4% per round trip, measured)
| test | result | file |
|---|---|---|
| EMA 9/24 cross, 39,428 signals | -0.01% before costs at 15 min | `docs/findings/learning_2026-10-17.md` |
| EMA 9/21 cross, 46,711 signals, 5-60 min | -0.02%..+0.01% gross; 9/21 = 9/24 | `docs/findings/ema_cross_check_2026-11-05.md` |
| trader's full setup on 9/21 (cross + volume + PVT + MACD + RSI) | +0.34% gross at 30 min (CI incl. 0), -0.46% net | same |
| 30+ signals combined, LightGBM, gappers 2-5 years | NO-TRADE (no out-of-sample expectancy) | `reports/decision/gappers_5y.report.md` |
| ORB on sub-$5 gappers (not the published rule) | -0.64% net/trade | `reports/decision/orb_2y.report.md` |
| news-catalyst buy-the-headline, 3,283 events | -1.37% net at 60 min | `reports/decision/news_events.report.md` |
| **alert fade (short retail-attention spikes)** | **+1.12%/trade held-out, PASS** | `docs/findings/prereg_alert_fade_2026-11-01.md` |
| fade x company news | no-news +1.70% vs news +0.02% (dev); confidence shown | `prereg_fade_news_2026-11-05.md` |
| fade x SEC filings (8-K today, offering in 30 d) | no reliable split | `prereg_fade_filings_2026-11-05.md` |
| fade exits (trail, VWAP, EMA 9/21, target) and holds (2 h, close) | all <= the 60-minute hold | `prereg_fade_exits/hold_2026-11-05.md` |

Reading: trend and oscillator signals carry ~no information about the next minutes-to-hour of small caps that is
larger than the cost to trade them. What carries information is WHO is buying and WHY: retail attention spikes
(fade them, unless there is real company news), and possibly institutional stocks-in-play at the open (testing).

## 4. Now running / next
1. ORB stocks-in-play replication (`prereg_orb_stocks_in_play_2026-11-05.md`, vault `orb-sip-2026-11`): training
   2021-2023 checks the replication; 2024-2026 (after the paper) decides, once, with measured spreads.
2. Signals on every trade: `sgt.fast.signals` snapshot at entry is shown on the Fade Tape (RSI/MACD panes, chips)
   and will be stamped on every live fade so the learning layer gets them forward (no look-ahead possible).
3. Fade brain v2 (to pre-register): one pooled model over the fade's alerts using the full signal set + news +
   filings, monthly walk-forward on training; decisive test forward in shadow. v1 (11 simple features) failed.
4. News sources: Benzinga via Alpaca (live), SEC EDGAR (live, needs the SEC contact on the runner), StockTwits public
   stream (collector exists, `sgt.ingestion.stocktwits_collector`), Reddit (needs an app registration), X (paid API,
   owner decision). No scraping of sites that block bots.

Sources: [Zarattini/Barbon/Aziz (SSRN via IDEAS)](https://ideas.repec.org/p/chf/rpseri/rp2498.html),
[Concretum summary](https://concretumgroup.com/a-profitable-day-trading-strategy-for-the-u-s-equity-market/),
[CXO Advisory rule summary](https://www.cxoadvisory.com/individual-investing/intraday-trading-of-overactive-stocks-via-opening-range-breakout/),
[QuantConnect replication thread](https://quantconnect.com/forum/discussion/18444),
[Ross Cameron 5-pillars filter script](https://kr.tradingview.com/script/mbqMf3pF-Ross-Cameron-5-Pillars-Filter),
[High float turnover manual](https://quantamentaltrader.substack.com/p/high-float-turnover-trading-manual),
[Dilution study (Substack)](https://riskpremiumresearch.substack.com/p/forecasting-dilution).
