# Answers to the red-day review: what fixes each weakness, what does not (2026-11-20)

Owner, 2026-11-20, on the seven weaknesses listed in `red_day_2026-11-19.md`: "find solutions to this attack for all
of them and use the web and research as a resource for enhancements and fixes to this overall mess".

For each weakness: what published research offers, what this project had already tested, what was tested tonight, and
what was done. Reads of stored records are description of data already seen. One registered test was run once
(section 6). Nothing in a live rule changed; one decision is put to the owner (section 5).

## Summary

| Weakness | Fix found? | What was done |
|---|---|---|
| 1. The model is a dip-buyer, not a stock picker | No. The published stock-picking rule for this horizon finds nothing on these 21 names | tested tonight, closed |
| 2. Its worst day is a drop that continues | Partly. Every filter that removes the bad tail removes the gain; sizing is what controls the tail | sizing decision put to the owner |
| 3. The live record is poor and too short to judge | Yes, as measurement | the nightly summary now says how unusual the live record is against the tested one |
| 4. A hedge removes the gain | No. Nothing tested here earns on a falling market | closed; the stop and the size are the protection |
| 5. The paper book swings more than the scorecard that decides it | Yes | 2.4 times, and for a worse return per unit of risk; owner's decision |
| 6. The repeat gate kept the fade out of two winners | No change is right. The one lead was tested on unseen data and failed | registered test, FAIL, gate confirmed |
| 7. A gate disagrees with its test | It does not, counted correctly. And yes, as measurement | the review's +11.8% counted repeated alerts of one stock; by the book's own unit no gate is against its test beyond chance. Every gate is now checked nightly, with a fixed reread count |

## 1. Not a stock picker

**Research.** The published way to pick stocks at a one-day to one-week horizon is short-term reversal: buy the
stocks that fell most against their peers, sell those that rose most. It is strongest when the market's and the
industry's move are taken out first ("residual" reversal), and the papers report it surviving costs in large stocks
up to about 2010
([Da, Liu and Schaumburg 2014](https://econpapers.repec.org/RePEc:inm:ormnsc:v:60:y:2014:i:3:p:658-674);
[Blitz, Huij, Lansdorp and Verbeek 2013](https://repub.eur.nl/pub/40707);
[de Groot, Huij and Zhou 2012](https://econpapers.repec.org/RePEc:eee:jbfina:v:36:y:2012:i:2:p:371-382)). None of
the sources found reports results after 2010.

**Tested tonight** on the model's own 21 names, 2020 to 2026, with the 15:50 prices the model itself uses: each day
the third of the names that fell most against the average name, against the third that rose most.

| Ranked by | Sample | Fallers beat risers by, a day | After costs |
|---|---|---|---|
| today's move against the average name | training, 1,630 days | +0.036% (not distinguishable from zero) | -0.044% |
| the same | later, 84 days | +0.036% | -0.044% |
| five-day move against the average name | training | +0.015% | -0.065% |
| the same | later, 84 days | +0.40% (84 days, not distinguishable from zero) | +0.32% |

Nothing there. The model's own long calls split the same way show no pattern either (calls on stocks that fell more
than the average name: +0.16% over the average name in training, -0.20% later).

**Verdict.** No stock-picking rule for these 21 names. Closed with the earlier results (section 6b of the playbook:
no rule ranks one call above another). A market-neutral book on a wide universe (hundreds of names) is a different
project with its own data and costs; it is not proposed from this evidence.

## 2. The worst day is a drop that continues

**Research.** Three published ways to cut the tail of a strategy: cut size when volatility is high
([Moreira and Muir 2017, re-examined by Cederburg, O'Doherty, Wang and Yan 2020](https://econpapers.repec.org/RePEc:eee:jfinec:v:138:y:2020:i:1:p:95-117):
the gain mostly disappears for an investor acting in real time); only buy dips while the market is above its 200-day
average ([CFA Institute blog, 2022](https://blogs.cfainstitute.org/investor/2022/02/08/technical-analysis-revisited-moving-averages-above-average-returns));
wait for the volatility-futures curve to normalise
([Cboe 2022](https://www.cboe.com/insights/posts/inside-volatility-trading-is-vix-backwardation-necessarily-a-sign-of-a-future-down-market):
the signal mostly fails to predict declines).

**Already tested here.** The 200-day filter: FAIL in the opposite direction (`prereg_long_market_filter_2026-11-16`:
the calls made below the trend earned +0.64%, those above it +0.06%; the filter would have cut the book's total
from +82.6% to +2.2%). The reason is the one in the red-day review: the model's whole gain is made after falls.

**Tested tonight** on the 2018 to 2026 record, the deciding ledger's arithmetic:

| Variant of the same long calls | A year | Sharpe | Worst drawdown | Worst day |
|---|---|---|---|---|
| the ledger as it is (1/21 of the book a call) | +7.6% | 1.08 | -5.0% | -2.4% |
| size cut when the names' volatility is high | +5.6% | 1.11 | -4.4% | -2.1% |
| never more than 6 calls' money on one day | +4.9% | 0.96 | -4.4% | -2.0% |
| only calls on a day the average name is down | +7.7% | 1.20 | -4.7% | -2.4% |
| only calls on a stock down 1% or more (the second ledger, live since 2026-11-20) | +6.0% | 1.09 | -3.9% | -2.3% |

Cutting size in volatile markets or capping the busy days gives up return for a slightly smaller tail: no gain.
Dropping the calls made on up days keeps the return on half the days, which is what the second ledger already
measures forward. No new rule comes out of this.

**Verdict.** The tail is the price of the gain; no filter separates them. What controls the size of a red day is how
much money each call gets (section 5).

## 3. The live record is poor and too short to judge

**Research.** Two standard tools: the minimum record length before a result means anything
([Bailey and López de Prado 2014](https://papers.ssrn.com/abstract=2460551)), and the drawdown a strategy of a
given quality should be expected to have
([Rej, Seager and Bouchaud 2017](https://arxiv.org/abs/1707.01457): both the depth and the length of normal
drawdowns are larger than people expect). Both say the same thing for 12 calls on three days: nothing can be
concluded yet, and a registered stop is the right guard.

**Built.** The nightly summary's long-calls line now ends with how unusual the live ledger is against its own
tested record: the share of same-length stretches of the 2018 to 2026 record that were this weak or weaker
(`sgt.megacap.longonly.rarity`; the record's daily ledger is `config/models/long_ledger_history.json`, built by
`scripts/build_long_ledger_history.py` with the live ledger's arithmetic). On 2026-11-19: 1.4% of three-session
stretches. It is a description said the same way every night; the registered stop (-5%) and date (2027-04-30)
decide, not this number.

## 4. A hedge removes the gain

**Research.** The books that earn when stocks fall are trend-following ones. The published intraday version for the
S&P 500 fund ([Zarattini, Aziz and Barbon 2024](https://concretumgroup.com/beat-the-market-an-effective-intraday-momentum-strategy-for-sp500-etf-spy/))
was reproduced here and FAILED on the 628 sessions after its sample (`prereg_intraday_momentum_2026-11-19`); it is
recorded forward, never traded. The model's own short calls: +0.01% a call. The model as a market timer: FAIL
(`prereg_dip_timer_2026-11-17`).

**Verdict.** No tested book here makes money on a falling market, and none is proposed: three attempts are closed in
the playbook. The protection of the long calls is the stop on the ledger and the size of a call.

## 5. The paper book swings more than the scorecard that decides it (owner's decision)

The registered test is decided by a ledger that gives each long call 1/21 of the book. The paper book sizes each
call by its own rule (1% of the book at risk over one day's range, at most 25% a stock, at most 100% invested):
most single calls get the full 25%.

That rule applied to the 2018 to 2026 record (the day's range taken as 1.6 times the 21-day daily volatility; the
beta cap, which trims busy days further, is not simulated):

| Size of a call | A year | Sharpe | Worst drawdown | Worst day | Days losing 2% or more |
|---|---|---|---|---|---|
| paper book today (up to 25% a stock) | +13.6% | 0.83 | -16.7% | -6.9% | 59 |
| 1/21 of the book (the ledger) | +7.6% | 1.08 | -5.0% | -2.4% | 3 |
| 2/21 of the book, at most 100% invested | +13.7% | 1.06 | -10.6% | -4.6% | 21 |

Why today's rule is worse for the risk it takes: it puts the same 25% into a lone call as into each of many.

| Day | Days | Paper book invests | and makes | Ledger invests | and makes |
|---|---|---|---|---|---|
| 1 to 2 long calls | 950 | 31% | -0.01% | 7% | 0.00% |
| 3 to 5 | 474 | 78% | +0.04% | 18% | +0.02% |
| 6 to 8 | 126 | 99% | +0.42% | 34% | +0.17% |
| 9 or more | 73 | 100% | +0.81% | 56% | +0.53% |

A third of the paper book's money is at work on the days with one or two calls, which earn nothing. The ledger's
size rises with the number of calls, and the number of calls is high exactly after falls, where the gain is.

Two more facts: the paper book's daily swing is 2.4 times the ledger's, and on the day the ledger first reached -5%
(the stop) the paper book under today's rule stood 15% under its high.

**Decision for the owner** (the size was set by the owner on 2026-11-14; the deciding ledger is not touched by any
of these):
- 1/21 a call: the paper book becomes the scorecard. The stop at -5% is then the book's stop. Smallest red days.
- 2/21 a call: the same tested return as today's size with a third less drawdown.
- keep today's size.

## 6. The repeat gate kept the fade out of two winners

**Research.** Stocks that draw repeated retail attention fall afterwards on average
([Barber, Huang, Odean and Schwarz 2022](https://econpapers.repec.org/RePEc:bla:jfinan:v:77:y:2022:i:6:p:3141-3190):
the most-bought stocks lose about 4.7% over the next 20 days), which argues for fading repeats; this project's own
test says the opposite for the channel's repeats within a week (the short gets squeezed). Nothing published was
found on multi-day runners specifically.

**Tested tonight.** The 317 training repeats split 14 ways by things known at the alert. One cell stood out:
repeats alerted the day before made +2.27% (n 93), repeats 2 to 7 days later -2.37% (n 224). The best of 14 cells
proves nothing, so it was written down as a registered test and run once on the unseen events
(`prereg_fade_repeat_day1_2026-11-20`):

| Held-out no-news repeats | n | Average |
|---|---|---|
| alerted the day before | 34 | -1.56% |
| alerted 2 to 7 days before | 99 | -1.25% |

**FAIL** (difference -0.32%, p 0.54). Day-after repeats lose on unseen data like the others. The lead is closed and
the gate is confirmed a second time on the held-out events.

**A correction to the red-day review.** It said the gate's 9 blocked alerts "would have made +11.8%". The book
fades a stock once a day, at its first alert; the 9 alerts are 7 stock-days (GP and OMH were each alerted twice
within minutes). Counted the way the book trades, the 7 average +8.4% with 5 winners, and the 95% lower bound of
that average is -2.4%: about a 1 in 20 draw under the tested record, on one of four gates watched every night. Not
evidence against the gate.

**Verdict.** The gate stays. Repeats are not split again on these files.

## 7. A gate disagrees with its test

**Built.** The nightly summary gets one line, "gates check": for each gate that is a choice (news, hours, repeat,
beta; not price under $1, borrow or halts, which a real account could not trade), the stock-days it stopped and what
those shorts would have made, each stock-day counted once by its first alert (`sgt.fast.would_have.gates_line`). A
gate is marked "AGAINST the gate", with its tested figure, only when its stopped shorts are winning by more than
chance: at least 5 stock-days and the 95% lower bound of their average above zero. A marked gate reads "reread now"
at 50 stock-days. The line lifts nothing: a reread is put to the owner under playbook section 0 (borrow in the
record, the loss cap, the owner's OK).

On the record to 2026-11-19 the line reads: news 42 at -1.4%, hours 10 at -4.9%, repeat 7 at +8.4%, beta 3 at
+12.8%; none marked. The first version of this check marked a gate on the sign of the average alone and would have
cried against three of the four on noise; it was tightened before it shipped.

Why 50: a repeat fade swings about 15% a trade, so the average of 50 is known to about 2 points, enough to tell the
tested -1.1% from the +2.5% of the fades the book takes.

## What was not found

No rule that makes any book green on a day the market falls 3%. The honest list of what protects the money: the
size of a call (section 5), the stop on the ledger, the gates of the fade (five losing shorts stopped on 11-19),
and the test size of the alert longs.
