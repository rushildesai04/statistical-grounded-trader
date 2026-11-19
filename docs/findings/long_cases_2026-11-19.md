# Long book cases: what the stock is doing decides the trade (exploration, 2026-11-19)

Owner, 2026-11-19: "it's very clear there is something wrong with the longs and the evidence shows it ... take your
time to actually explore the problem and find a fix and add on top of the complexity and cases for LONG book so we
can account for as many cases as possible in the market."

**What this is.** An exploration, not a pre-registered test. Both the training alerts (2025-01-03 to 2026-06-30,
2,455 alerts) and the later ones (2026-07-01 to 2026-11-04, 822 alerts) were looked at, several entries and exits
were compared, and the best one was picked. Numbers picked that way flatter the winner. Nothing here is a finding;
the only clean test of the new entry is the live book at test size. All results are per trade after estimated costs
on the official tape unless a line says otherwise.

## 1. What was wrong with every earlier long test
Every earlier test asked one question: "one rule for every alert, does it make money?" And every rule bought a
ONE-MINUTE structure (the first red candle, a break of the last pivot) with its stop at one candle's low, within
minutes of the alert. The answer was always no, and the reviews stopped there. The live losses kept showing the
same picture, and the tests could not see it because none of them looked at what the stock did AFTER the alert
before buying:

| The book until 2026-11-19 (official tape) | Training | Later period |
|---|---|---|
| Trades | 2,133 | 689 |
| Per trade | -2.05% | -2.32% |
| Wins | 16% | 14% |
| Stopped in the minute it was bought | 45% | 47% |

A stop one candle wide on a stock that moves 3% a minute is hit by noise. The trade was over, at a full loss, before
the idea could be right or wrong.

## 2. Cases and actions tried
For each alert: the situation (company news and its kind, price, time of day, halt) and the result of five different
actions, each decided only from candles that had closed.

| Action | What it waits for | Stop | Training | Later period |
|---|---|---|---|---|
| the book until now | first one-minute setup to fill | one candle's low | n 2,133 · -2.05% · 16% wins | n 689 · -2.32% · 14% wins |
| **base break** | 10 minutes in a range of at most 8% after the alert, then a candle CLOSES above it and above session VWAP (within 60 minutes) | under the whole base (median 5.6% away) | **n 886 · -0.86% · 35% wins** | **n 261 · -1.30% · 30% wins** |
| flush and reclaim | 8% under the alert price, then a close back above VWAP | under the flush low | n 882 · -1.52% · 34% | n 275 · -2.80% · 29% |
| held up | 15 minutes later still above the alert price and VWAP, within 5% of the day's high | the low since the alert | n 466 · -2.84% · 32% | n 141 · -1.86% · 35% |
| new high | five candles under the day's high, then a close above it | under those candles | n 1,089 · -1.94% · 34% | n 327 · -2.62% · 30% |

All four "wait for the stock to prove itself" actions win about twice as often as the book and are almost never
stopped in the entry minute (1 to 3%). Only the base break also loses clearly less in both periods.

**The same alerts, both ways.** Where a base forms and both rules trade: book -1.41% vs base break -0.69%
(training, 798 alerts); -2.06% vs -1.46% (later, 224). Where NO base forms the book lost the most: -2.44% in both
periods (1,335 and 465 trades). Waiting for the base skips those alerts: about two alerts in three get no long.

**Exits on the same entries** (2R target and 30 or 60 minutes, 1R, hold to the close): none is clearly better in
both periods. The exit is not the problem; the entry was.

**The base break as the code now runs it** (`sgt.fast.patterns.base_break`; 2R target, 30 minutes):

| | Training | Later period |
|---|---|---|
| found and traded on the official tape | n 874 · -0.79% · 36% wins | n 260 · -1.12% · 33% wins |
| found on the free live feed's candles, traded on the official tape (what the live book does) | n 846 · -0.82% · 35% wins | n 235 · -1.29% · 30% wins |

The thin live feed does not break it: it finds nearly the same trades. Before costs the entry is about flat
(0.00% / -0.41%); the round trip costs about 0.9%. The buy comes a median 30 minutes after the alert.

## 3. Situations (base break only; small cells, read as leads)
| Situation | Training | Later period | Read |
|---|---|---|---|
| company news in the window | n 363 · -0.35% | n 78 · -1.01% | better than no news in both |
| no company news | n 523 · -1.22% | n 183 · -1.42% | the worst longs under every action tried |
| news kind: offering | n 29 · -2.58% | n 3 · -3.92% | worst kind in both; far too few to rule on |
| news kind: deal / contract | n 56 · -1.77% | n 10 · -2.90% | bad in both; few |
| news kind: clinical / FDA | n 36 · +0.86% | n 7 · +2.03% | positive in both; 43 trades in all |
| price $5 and up | n 332 · -0.28% | n 73 · -0.14% | best price band on the tape; did not hold on the live feed's candles |
| price under $5 | n 554 · -1.22% | n 188 · -1.75% | |
| 10:30 to 13:00 | n 308 · -0.18% | n 79 · -0.82% | |
| first hour | n 470 · -1.15% | n 135 · -1.99% | |

No situation is turned into a rule here: each would be a cell picked after looking. They are the cases the live book
will be scored by, once every alert carries its news read at the time of the alert (the next change: the news,
filings and social connectors today run only for alerts the short book enters).

## 4. What changed in the live long book
- Entry: the base break (L8), plus the bull flag breakout (L5v2; 27 and 13 first fills in the two periods, about
  flat, rarely stopped in the entry minute). The first to fill per alert.
- No longer bought live: first red candle (L4a, L4b), break of structure (L6), halt-reopen red candle (L7a, L7b).
  They stay in the nightly shadow measurement on every alert, so the old and the new entry are compared on the same
  alerts from now on.
- Stop under the whole base, 2R target, 30 minutes. No red-candle exit for a base-break long (that exit was tested
  on the one-minute setups; trades are stamped `rules = B1`).
- The alert is watched for 60 minutes after its start bar (was 30).
- Size, costs, the skip of a target that cannot beat costs, the heads-up text, the official nightly result and the
  "no texted trade is cut" rule are unchanged.

## 5. What to expect, plainly
On past alerts the base break still LOSES after costs: about -0.8% to -1.3% a trade, 30 to 36% wins. That is roughly
half the old book's loss and twice its win rate, not a profit. It runs at test size ($500 a long) for that reason.
The book is "proven" only by 300 settled live trades with the 95% interval of the mean above zero
(`long_book.proven`); nothing else raises the size.

## 6. Limits
- Picked after looking at both periods (above). The later-period numbers are not a clean confirmation.
- The paper fill is the open of the first candle after the break candle closes. A real market order sent when that
  candle closes fills a few seconds into that minute; on a fast stock that can differ from the open.
- The news kinds come from headline patterns (`scripts/gap_news_study.py`), a rough classifier.
- Scratch code and tables of this exploration are not in the repo; the detector, its tests and this note are.
