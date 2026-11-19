# Pre-registration: pre-market gaps by kind of news, a scanner input (2026-11-18)

Status: FROZEN before the script exists and before any number below is computed. Script
`scripts/gap_news_study.py`, run ONCE on training. Report only.

## Why
Owner, 2026-11-18, on the scanner as a second source of signals: "let's do both" (borrow-list changes, and
pre-market gaps split by the kind of news). Price-and-volume events and earnings follow-through found nothing. A
gap at the open is a different moment than the channel's alerts (which arrive a median 45% into the move), and the
reason for the gap is information a chart does not hold: an offering and a contract win do not trade alike.
Tested nearby and lost: the opening-range breakout on stocks in play, fading our own spikes, every long on the
channel's alerts. Never tested: the direction of a gapper from its open, by kind of news.

## Events
`data/universe/gappers_2y.csv` (stocks opening 10% or more above the previous close; 2024-10-01 to 2026-10-16)
with a previous-day volume of at least 50,000 shares and stored official 1-minute candles: 9,783 stock-days.
Training: sessions before 2026-04-01 (6,311). Held-out: from 2026-04-01 (3,472), opened once for what passes.

## Kind of news (from the local headline archive `data/news`, known before the open)
Articles tagging the stock (and at most one other symbol: a roundup is not company news) created from 16:00 ET of
the previous weekday to 09:30 ET of the gap day. The kind is the first of these that any such headline matches:
1. OFFERING: offering, priced, pricing of, private placement, registered direct, warrant, at-the-market, shelf,
   convertible, dilut-.
2. EARNINGS: the alert study's earnings pattern (`sgt.fast.news.EARN`).
3. CLINICAL: fda, phase 1/2/3 (also i/ii/iii), trial, clinical, topline, approval, clearance, patent.
4. DEAL: merger, acquire-, acquisition, to be acquired, buyout, agreement, contract, partnership, collaboration,
   award, order.
5. ANALYST: upgrade, downgrade, price target, initiates, reiterates, maintains.
6. OTHER: news, none of the above.
7. NONE: no such article.

## The trade that scores a gap
From the first regular-hours candle's OPEN, long (the gap runs) or short (the gap fades), to the close of the
candle one hour later (the one starting 10:29, or the last before) and to the session's close. Cost: the measured
gapper round trip at the open (`GAPPER_COST_MODEL`, opening-hour multiple), plus 0.5% for a short.

## Decided (training; run once)
Seven kinds x two exits, each tested long and short: 28 one-sided tests at alpha = 0.05 / 28, bootstrap by session
date (10,000 resamples, seed 20261118; a cell under 150 gaps or without a bound fails). A cell PASSES in a
direction when the lower bound of its mean net is above 0.

## Reported, not decided
Per kind: gaps, share of all gaps, mean gross and net both ways, win rates; by gap size (10-20%, 20-50%, above
50%); by price ($1-5, above $5; under $1 apart); the same split for gaps with a previous-day volume under 50,000.

## What follows
- A pass: that cell alone is scored once on the held-out sessions; a pass there goes to the owner as the scanner's
  first live signal (each morning: the gappers of that kind, texted before the open, paper-traded with this
  entry and exit).
- No pass: recorded; gaps by kind of news are not a scanner input.

## Known limits
The open is taken at the first candle's open: a real order pays the opening spread (the cost model's opening
multiple stands for it) and, for a short, needs shares to borrow, which history cannot show. Headlines are
classed by words, not read. The gapper list was built from today's symbols: stocks delisted since are missing.

## Result (2026-11-18, training run once; `reports/gap_news/result_train.json`)
Plan and code as committed before the run. 6,311 gaps, 375 sessions.

**No cell passes, long or short.** The held-out sessions stay closed.

| kind of news | share of gaps | gaps | move from the open to the close | long, net | short, net (lower bound) |
|---|---|---|---|---|---|
| none | 62% | 3,882 | -0.76% | -1.84% | -0.82% |
| earnings | 20% | 1,268 | +0.00% | -0.93% | -1.43% |
| other news | 8% | 519 | -2.03% | -3.12% | +0.43% (-2.38%) |
| deal, contract | 4% | 270 | -4.13% | -5.22% | +2.54% (-1.23%) |
| clinical, FDA | 3% | 196 | -1.99% | -3.10% | +0.39% (-10.78%) |
| offering | 2% | 107 | -3.46% | -4.50% | +1.92% (too few to test) |
| analyst | 1% | 69 | -2.01% | -3.09% | +0.43% (too few) |
| all | | 6,311 | -0.96% | -2.01% | -0.60% |

Reading:
1. A stock that gaps up 10% or more falls from its open on average (-1.0% in the first hour, the same by the
   close); 38% to 39% are higher at the close than at the open. Buying a gapper at the open loses 2% after costs
   whatever the news.
2. The kind of news does sort them: earnings gaps hold (0.0%), gaps on a deal or contract headline give back
   4.1% by the close, offerings 3.5%. None clears the bar after costs and the 0.5% for borrowing: the best, the
   short of a deal gap to the close, makes +2.54% a trade with a lower bound of -1.23% across 28 tests.
3. Leads, not results: (a) deal and contract gaps fade (270 gaps); (b) offering gaps fade hardest (107, too few
   to test, -10.8% when the gap is above 50%); (c) gaps of 20 to 50% with NO news were up 2.1% by the close
   (767 gaps) while smaller and larger ones fell. Each would need its own frozen plan, and the two fades need
   shares to borrow at the open, which is where the alert fade already stands.

Verdict: recorded. Gaps by kind of news are not a scanner input as tested.
