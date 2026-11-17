# Amendment to the live long book: no long whose target cannot beat costs; measure-only with a "seen" text (2026-11-17)

Not a statistical test. Two owner decisions of 2026-11-17 ("yes continue with the calls and your recommendations, all
of them"), written down with the evidence each rests on.

## 1. No long whose 2R target cannot beat the estimated cost (decision 4)
Found with the replay lab on MPU 2026-11-17: by the rules the book buys 1.51 with a stop at 1.50 and a target at 1.53,
on a stock whose estimated round trip costs 1.3%. If the target is hit the trade makes +1.3% before costs: zero
after. If the stop is hit it loses. Such a trade cannot win.

Rule (`sgt.fast.long_book.no_win`): at the fill, `2 x (entry - stop) / entry <= est_costs.cost("long", symbol, fill
time, entry)` -> the fill is not taken. The book then takes the next setup of the same alert that can win, if one
fills inside the window; if only no-win setups filled, the alert's row is marked `nowin` (no trade, no text).

Evidence (descriptive, on data already seen: `reports/long_risk/base.csv`, the A2 long book of
`prereg_long_risk_2026-11-12`, with the same cost estimate):

| | trades | won | mean per trade | sum |
|---|---|---|---|---|
| training: target cannot beat costs | 211 of 2,242 (9%) | 1 (a gap fill above its target) | -1.26% | -2.7 |
| training: the rest | 2,031 | 15% | -2.18% | -44.2 |
| held-out: target cannot beat costs | 52 of 724 (7%) | 1 | -1.20% | -0.6 |
| held-out: the rest | 672 | 14% | -2.30% | -15.4 |

197 of the 211 ended on the stop, 14 on the target (a loss after costs by construction). 17% of all long trades had
a stop two cents or less from the entry.

What this is and is not: a dominance argument (the best case of the trade is not a gain), not a discovered edge. It
removes trades that lose by construction; it does NOT make the long book profitable (the remaining trades still lose
about 2.2% each). The official record of trades already taken is not restated. The nightly long shadow (each tested
setup, as tested) is unchanged, so the research measurement of every setup continues.

## 2. Measure-only with one "seen, not traded" text (decision 1B)
The long book has lost in every tested slice (A2: -2.09% per trade over 2,242, 14% won) and live (10 trades to 11-13:
-$1,212; 11-17: four trades, about -$182). The owner had it back on from 11-16 to see every alert on the phone.
Choice of 11-17: keep the phone informed, take the losing paper trades out of the totals.

While `long_off` is set: the book still watches every alert; the long it WOULD have opened (first setup to fill that
can win) is stored with status `seen` and texted once ("LONG SEEN, not traded", with entry, stop, target). No
position, no exit text. `seen` rows are in no total (book value, digest, midnight and weekly summaries, the live
monitor all read open / est_closed / settled). The nightly settle replays the watched order on official bars and
stores what it would have made on the row (status stays `seen`), shown in the digest ("longs seen, not traded") and
the site's alert list. The 09:20 notice says "measure-only" each morning it stays so. Back on only by the owner's
word, for a rule that passed its test.
