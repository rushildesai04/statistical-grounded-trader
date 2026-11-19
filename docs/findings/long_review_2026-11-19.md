# Long book review: what the trades, the tests and outside research say (2026-11-19)

Owner, 2026-11-18 night: "do some research and see where you can improve longs ... based on what logic we have and
what other people are doing ... so we don't lose money based on the trades we have made and all of the comments and
notes we have." A review, not a new test: every number below is a stored result or the live record. No study was
re-run and no held-out sample was opened.

## 1. The live record (2026-11-10 to 2026-11-18, official results)
17 longs, 6 wins, -2.18% per trade after costs, average size $4,280. Long book value $98,505 (-$1,495).

| What the trades show | Trades | Result |
|---|---|---|
| Longs after a halt ("resumed" alerts, halt-reopen setups) | POAS -14.5%, FCUV -9.5%, OMH -2.5%, RETO +10.2% | 4 trades carry 42% of all loss points; swings of 10% either way |
| Longs in the first 10 minutes of the session | GEVO +0.8%, ADXN -3.8%, POAS, SLSN -4.0%, GP +2.3%, SPAI -2.7% | 2 wins of 6 |
| The phone showed a win, the official tape a loss | ADXN (+6% shown, -3.8% official), XLAB (+5.9% shown, -5.0%) | the thin live feed flatters |
| The trade was over when the text arrived | 8 of 14 priced texts (through the stop, or past the target) | the text is about a minute behind the fill |
| By setup | first red candle 1 of 6; red-candle break 3 of 7; break of structure 2 of 2; halt reopen 0 of 2 | too few to rank setups |

The 13 notes stored on these trades say the same thing in each loss: bought after the spike
had already turned, stopped in the entry minute. Their "best exit in hindsight" lines are hindsight (playbook 3.6).

## 2. The tested record (stored results; training 2025-01-03 to 2026-06-30, held-out 2026-07-01 to 2026-11-04)
On the official tape the book's rules make 2,139 trades in training: -2.07% per trade, 15.6% wins. Held-out: -2.32%.

| Setup (official tape) | Train n | Train net | Train wins | Held-out n | Held-out net | Held-out wins |
|---|---|---|---|---|---|---|
| L4a first red candle, next open | 1,045 | -2.01% | 13% | 357 | -2.42% | 10% |
| L4b first red candle, break of its high | 553 | -2.59% | 16% | 172 | -1.99% | 20% |
| L6 break of structure | 441 | -1.52% | 20% | 118 | -2.59% | 14% |
| L7a / L7b halt reopen | 73 | -2.7% / -4.0% | 10% / 24% | 29 | -2.3% / -4.6% | 16% / 10% |
| L5v2 graded bull flag | 27 | +0.22% | 26% | 13 | +0.43% | 38% |

About thirty long ideas have been tested on these alerts (playbook section 4): buy every alert, calm alerts, later
alerts, first red candle, bull flags (three detectors), break of structure, halt reopens, sudden drops, the trader's
five rules as a gate, his matrix as an exit, his matrix as the whole rule, runner exits, breakeven stops, wider and
padded stops, entry timing from 0 to 90 minutes, six "where in the move" scenarios, sixty slices. None is positive
in both samples. Most lose BEFORE costs.

The trader's own rule, tested faithfully (16,643 round trips): before costs -0.21% per round trip, after costs
-1.15%, 24% wins. Where it loses: the first entry after the alert (-1.58% before costs) and the first hour
(-1.22%). Later entries and the afternoon are about zero before costs; a round trip costs 0.7% to 1.1%.

## 3. Why the longs lose (the reasoning)
1. **The stock falls after the alert, on average.** That is the fact the short book was built on (+1% per short in
   the tested years; holding long for 0 to 90 minutes loses 1% to 3%). A long swims against it.
2. **The trades are one-to-ten-minute scalps on stocks where a round trip costs about 1%.** The move the rules catch
   is zero or negative before costs, so no exit or stop can rescue it (wider stops lose more; padded stops gain 0.1
   to 0.3 points).
3. **The entry is right about one time in six.** A rule that wrong is not a tuning problem.

## 4. What other people do (web search 2026-11-19; secondary summaries, not checked against the papers)
- **Momentum day traders who buy these stocks** select hard and by hand: price $2 to $20, small float, 5x relative
  volume, a real catalyst, the first hour or two, and they pass on most candidates. The write-ups give no test
  results. Our trader says the same in his words: "SKYA ... actually had legs, most don't."
- **Research on attention stocks:** heavy retail buying is followed by falls (Robinhood users' top buys: about -3%
  to -6% abnormal return over five days, Barber, Huang, Odean, Schwarz); large overnight gaps tend to give back
  during the day. Same direction as our data.
- **Research on day traders:** most lose; in the Brazilian study of people who kept at it for 300 days, about 97%
  lost.
- **Where published long-side evidence exists, it is in liquid instruments:** the first half-hour's move of SPY
  predicts its last half-hour (Gao, Han, Li, Zhou, Journal of Financial Economics 2018); an intraday trend rule on
  SPY (Zarattini, Aziz, Barbon 2024: 19.6% a year, Sharpe 1.33 claimed for 2007 to 2024; a community re-run found
  much less). Their opening-range rule on "stocks in play" claims far more; our faithful replication of it failed
  (`prereg_orb_stocks_in_play_2026-11-05`).

## 5. What follows
Not supported by anything above: another entry, exit or stop variant on the same alerts.

Three levers that are real:
1. **Size.** The only change certain to cut the dollars lost while every long still trades and is texted. At the
   live average (-$88 per long at $4,280) a $500 long loses about $11.
2. **The trader's method, built as his rule.** Needs his answers (which alerts he takes, whether he waits out the
   first green move, which charts must be green, all out or part out, his stop). His matrix and his "MACD goes red"
   exit are recorded on every live long since 2026-11-19.
3. **A long-side source where costs are tiny and our live feed is accurate** (index funds, mega-caps), starting
   with a pre-registered test of the published intraday trend rule on our own data. Paper only.

Leads, forward-only, never to be read as findings: the graded bull flag (L5v2) is the one setup not negative in
both samples, on 40 trades in 22 months; entries later than the first green move after an alert, and after the
first hour, are about zero before costs instead of clearly negative.

## Sources
- Zarattini, Barbon, Aziz, "A Profitable Day Trading Strategy for the U.S. Equity Market": https://concretumgroup.com/a-profitable-day-trading-strategy-for-the-u-s-equity-market/
- Zarattini, Aziz, Barbon, "Beat the Market: An Effective Intraday Momentum Strategy for S&P500 ETF (SPY)": https://www.sfi.ch/fr/publications/n-24-97-beat-the-market-an-effective-intraday-momentum-strategy-for-s-p500-etf-spy
- Gao, Han, Li, Zhou, "Market intraday momentum": https://econpapers.repec.org/RePEc:eee:jfinec:v:129:y:2018:i:2:p:394-414
- Barber, Huang, Odean, Schwarz, "Attention Induced Trading and Returns" (summary): https://gsm.ucdavis.edu/news/what-effect-brokerage-robinhood-having-stock-market
- "Retail Day Trading is an Uphill Battle" (summary of the day-trader studies): https://quantpedia.com/retail-day-trading-is-an-uphill-battle/
- Concretum, "Identifying stocks to fade": https://concretumgroup.substack.com/p/identifying-stocks-to-fade
- Practitioner checklists: https://daytradingz.com/gap-and-go-strategy/ and https://daytradingtoolkit.com/strategies/low-float-runner-trading-strategy
- Lou, Polk, Skouras, "A tug of war: overnight versus intraday expected returns": https://researchonline.lse.ac.uk/id/eprint/119010
