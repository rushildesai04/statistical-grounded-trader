# Pre-registration: do the long book's high-cost trades earn their cost? (2026-11-20, SVRE loss review)

Status: FROZEN before any held-out outcome is computed. Script: `scripts/long_cost_cap_study.py`, run ONCE.

## Source
SVRE, 2026-11-20: the first live base break under the 4% width cap, bought $1.28 at 10:03 ET, stopped at $1.19 two
minutes later (-8.3% after an estimated cost of 1.3%; $191 test size). Owner, the same morning: "did you think about
researching different rules that would have helped in this scenario and then adding that logic".

## What was looked at before this plan (disclosed)
All on the TRAINING alerts only, at the bot level (the long book itself replayed on the free feed's candles at the
live 4% cap, official results: `long_bot_replay_2026-11-19.md`; 506 trades, -0.69% a trade, 34% wins):

| Looked at (known at the entry) | Result on training | Read |
|---|---|---|
| base under the day's high | -0.39% against -0.26% (n 292 / 110) | nothing |
| bought 3%+ above the base top | +1.09% (n 57) against -0.62% near the top | opposite |
| break candle under 300 shares on the feed | -0.50% against -0.08% to -0.66% | nothing |
| 3+ quiet minutes before the break | +3.31% (n 21) | opposite, few |
| stop 6%+ away | +0.80% to +5.11% (n 40) | opposite |
| company news / no company news | -0.88% / -0.52% (n 236 / 270) | the tape-level lead (news better) reverses |
| before 10:30 / 10:30-13:00 / after 13:00 | -0.85% / -0.35% / -1.07% | weak |
| price $0-2 / $2-5 / $5-10 / $10+ | -0.44% / -1.05% / -0.12% / -1.15% | no order |
| **estimated cost above 1%** | **-1.24% (n 111) against -0.53% (n 395)** | the one consistent lead |

Nine looks, so the best one proves nothing by itself. What sets the cost lead apart is that it is arithmetic, not
a pattern: before costs the three cost bands earn the same (+0.04%, +0.17%, +0.01% a trade), so the whole
difference is the cost that is charged. The cost is the gapper spread model (`est_costs.cost`): above 1% means a
stock under $5 bought before 10:30 ET (1.25% round trip). SVRE was one. The rule is break-even before costs
(`long_cases_2026-11-19.md`), so where the cost is highest the loss is largest.

Earlier, at the tape level and with another threshold (`long_cases_2026-11-19.md` section 7): cost at or under 0.7%
-0.35% (training) and +0.40% (later period, n 30) against -0.87% and -1.31%. The held-out alerts have therefore been
seen split by cost once, on the tape-level simulation at 0.7%; never at the bot level and never at 1%. This is
disclosed: the test below is a check on partly seen data, and the forward record decides.

Research read the same day: jumps without news reverse within the hour and jumps with news continue
([Xiao, Yin and Zhao 2020](https://law-journals-books.vlex.com/vid/jumps-news-and-subsequent-855590156);
Jiang, Li and Wang, "News Momentum"), which is the fade's own edge; on our long trades at the bot level the news
split does not show it (above). Momentum profits sit in the stocks that cost the most to trade
([Lesmond, Schill and Zhou 2004](https://www.bauer.uh.edu/rsusmel/phd/Lesmond_et%20al%20_2004_JFE.pdf)). No published
base rate was found for failed intraday breakouts in small stocks; volume confirmation has no tested support.

## Population and data (no new choices)
The bot replay's held-out trades at the live configuration (alerts from 2026-07-01, settled trades, both setups the
book runs: base break and L5v2), cost = the round-trip cost the official result charged. November's replayed trades
are reported beside them, never deciding (about 20 trades).

## The one test
Statistic: mean net of the trades with cost at or under 1% minus mean net of the trades with cost above 1%, held-out,
sessions resampled jointly (10,000 resamples, seed 20261112, `trader_rules_long_study.boot_groups`).

PASS only if ALL: at least 30 held-out trades above 1%; their mean net is below 0; the statistic is above 0.
The one-sided p is reported, not required: the claim is a deduction (the same result before costs, a higher cost),
the sample cannot resolve half a point (about 150 trades with a spread of 6% a trade), and a requirement of p < 0.05
would fail whether the claim is true or not. A PASS therefore says "the unseen trades do not contradict it", no more.

## What each outcome means
- PASS: a candidate case rule is put to the owner: the long book does not buy when the estimated cost is above 1%
  (a stock under $5 before 10:30 ET); the alert is still listed with what the long would have made, and the nightly
  record of the skipped longs is read at 50. It makes the book lose less. It does not make it a winner: no band is
  positive after costs.
- FAIL: closed. No rule from this loss. The cost split is not looked at again on these alerts.

## Not done
No other threshold, no combination with time, news or price, no change of the entry or the exit, no re-run.

## Result (run once, 2026-11-20; `reports/long_cost_cap/result.json`) - PASS as registered, weak
| bot replay, live configuration | trades | cost above 1% (would be skipped) | cost at or under 1% (kept) | difference | p |
|---|---|---|---|---|---|
| held-out (decides) | 150 | n 33 · -1.58% | n 117 · -0.61% | +0.96 points | 0.17 |
| training (disclosed) | 506 | n 111 · -1.24% | n 395 · -0.53% | +0.71 | 0.16 |
| November (reported only) | 21 | n 6 · +0.10% | n 15 · -0.30% | -0.40 | 0.59 |

Before costs on the held-out trades: -0.33% above the cap, +0.04% under it: the dear trades were no better before
costs either. All three conditions hold, so the candidate rule is put to the owner. Read it for what it is: the
unseen trades do not contradict the arithmetic, the difference is not statistically established (p 0.17), and
November's 21 trades point the other way. The kept trades still lose (-0.61%): the rule would make the long book lose
less, not win. Nothing is live from this test; the cost split is not looked at again on these alerts.

## What the official tape showed about SVRE itself (read after the plan was frozen; not part of the test)
The base the bot bought did not exist. In the ten minutes before the buy the free feed showed 6 candles in a range of
3.4% on 1,396 shares; the official tape showed a range of 10.3% ($1.1875 to $1.31) on about 164,000 shares. The real
break was at 10:00 ET (to $1.31); the feed had no trade from 09:59 to 10:01, the bot saw a "break" at 10:02, bought at
10:03, and the tape fell from $1.265 to $1.18 at 10:04 on 101,681 shares. On the full tape there is no base and no
trade. This is the phantom base of `long_bot_replay_2026-11-19.md` (November: 12 of 31 bot trades, -2.8% a trade);
the 4% width cap is measured on the feed and passed it. None of the feed-only signs tried above (few candles, few
shares, quiet minutes) separates such trades. The fix is a real-time full-tape source (broker market data).
