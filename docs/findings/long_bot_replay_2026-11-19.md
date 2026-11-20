# The long book replayed as the bot: old logic against new, and what the free feed does to the new entry (2026-11-19)

Owner, 2026-11-19 night, after all three longs of the day lost: "go analyze and see if the new logic we have will
work and actually simulate with historical data with our bot so we can compare results. Always challenge the tests
and logic so we can see adversarially if our implementation is optimal."

**What this is.** Not a study re-implementation. `scripts/long_bot_replay.py` gives each past alert its own ops
database and runs the live code on it: `long_book.tick` once a minute on the free feed's candles that had closed
(entry, no-win skip, size, heads-up, exits), then the nightly `long_book.settle` and `text_order.run` on the official
tape. Two configurations on the same alerts: OLD = the one-minute setups the book bought until 2026-11-19, NEW = the
base break. Results are official results after costs.

**Is the replay the bot?** Its OLD configuration reproduces the live book's 21 settled trades (2026-11-10 to
2026-11-19) with the same setup in 21 of 21 and the identical official result in 17. The four that differ are rule
versions older than today's code (KULR, POAS 11-12) and two "bought at the text" trades priced from stored prints
live and from the next candle's open here (VTSI, GMM).

## 1. The answer: the new logic loses about a third of what the old one loses, and does not make money
| Sample | OLD (one-minute setups) | NEW as shipped (base up to 8% wide) |
|---|---|---|
| Training, 2,372 alerts | 1,570 trades · -1.29% · 21% wins · -$8,111 at test size · 0 of 18 months up | 876 trades · -0.89% · 33% wins · -$2,238 · 4 of 18 months up |
| Later period, 774 alerts | 520 trades · -1.83% · 18% wins · -$3,117 | 237 trades · -1.01% · 32% wins · -$845 · 1 of 4 months up |
| November 2026, 96 alerts (chose nothing) | 48 trades · -2.54% · 17% wins · -$364 | 31 trades · -1.85% · 35% wins · -$101 |

- The typical NEW trade loses: median -2.9% / -2.8% / -2.0%. The average is held up by a few large winners; without
  its best 5% of trades NEW makes -1.97% / -1.90%.
- On the same alerts both trade, NEW is a little better in the two large samples (-0.88% vs -1.07%; -1.01% vs -1.56%)
  and not in November (-1.62% vs -1.42%, 25 alerts). Most of the gain is the trades NEW does not make: the alerts
  only OLD trades lose -1.48% / -1.98% / -3.75%.
- Today (2026-11-19), official: OLD really traded POAS -6.76%, GP -1.70%, MRKR -1.31%. NEW on the same alerts, bot
  replay: POAS no trade, GP -4.3%, MRKR -5.6%, NCPL -0.5%. A red day under either.

## 2. The flaw found in our own implementation: the free feed shows bases that are not there
The bot decides on the free feed, which carries about 1% of the real volume in these stocks (median 0.9% of the
shares in the base). With so few prints, every base looks narrower than it is.

| November base-break trades of the bot (31) | Trades | Result |
|---|---|---|
| a real base on the official tape (up to 8% wide there too) | 19 | -1.24% · 42% wins |
| a phantom base (wider than 8% on the tape; FCUV: 5.6% on the feed, 23.3% on the tape) | 12 | -2.81% · 25% wins |
| same alert, found on the tape vs found on the feed (23 alerts) | | +0.44% vs -0.46% |
| trades that exist only on the feed | 8 | -5.83% |

The earlier check of this ("found on the free feed, traded on the tape: -0.82% / -1.29%") compared averages and
missed it: the average hid that a third of the trades were different trades.

## 3. The stop-gap, tested with the whole bot
A base must be narrower on the feed to be a base on the tape. The width cap as the feed shows it:

| Cap | Training | Later period | November |
|---|---|---|---|
| 8% (shipped) | 876 · -0.89% · -$2,238 | 237 · -1.01% · -$845 | 31 · -1.85% · -$101 |
| 6% | 711 · -0.87% · -$1,906 | 199 · -0.93% · -$740 | 29 · -0.47% · -$56 |
| 5% | 603 · -0.84% · -$1,638 | 176 · -0.98% · -$667 | 24 · -0.61% · -$57 |
| **4% (now live)** | 506 · **-0.69%** · -$1,287 | 150 · **-0.83%** · -$557 | 21 · **-0.19%** · -$43 |
| 8 feed candles instead of 5 | 655 · -1.28% | 154 · -0.60% | 20 · -0.87% (does not hold: rejected) |

The trades the 4% cap drops are the worse ones (-1.42% in training, -1.01% later). Better in all three samples, by
a small amount, and still a loss. The real fix is a full-tape live feed (the owner's broker market data), under
which the entry is the tape-found one.

## 4. Is the rule fitted to its four numbers? No. Is any version profitable? No.
The rule and its neighbours on the official tape (ideal data), 2R target, 30 minutes:

| Base length \ width | 4% | 6% | 8% | 12% |
|---|---|---|---|---|
| training: 5 min | -0.41% | -0.57% | -0.66% | -1.14% |
| 10 min | -0.39% | -0.54% | -0.71% | -0.76% |
| 15 min | -0.33% | -0.62% | -0.33% | -1.10% |
| 20 min | -0.63% | -0.32% | -0.36% | -1.02% |
| later: 5 min | -1.05% | -0.95% | -1.09% | -1.67% |
| 10 min | -1.11% | -0.89% | -1.04% | -1.52% |
| 15 min | -0.88% | -1.14% | -1.23% | -1.62% |
| 20 min | -1.49% | -1.08% | -0.95% | -1.11% |

Neighbours behave like the shipped cell: the result belongs to the idea, not to a lucky setting. And no cell is
positive in either large sample; other targets and holds (1R, 3R, 15 and 60 minutes) change nothing. On perfect data
the base break earns about nothing before costs and loses the cost. November's positive cells are 10 to 30 trades.

## 5. What this says plainly
1. The long side of these alerts has no tested edge. The base break is the least bad long found: about a third of
   the old book's dollar loss, because it trades less, sizes smaller and is right twice as often.
2. It runs at $500 a trade because it is not proven, and nothing here suggests the 300-trade proof will pass.
3. The largest remaining gap between what we test and what the bot does is the feed. Until a full-tape live feed is
   connected, the width cap is the patch.
4. One lead survives from earlier the same day: base breaks whose round trip costs 0.7% or less were about
   break-even (169 trades). It is recorded on every live long; the live record decides.

Limits: the training and later samples were looked at when the entry was chosen (November was not); the replay has
no stored prints, so a "bought at the text" trade is priced at the next official candle's open; costs are the
model's.
