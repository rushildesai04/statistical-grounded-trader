# Pre-registration: the daily model's long calls on large caps it has never seen (2026-11-17)

Status: FROZEN before any 15-minute bar of a fresh name is fetched and before any model number of a fresh name is
computed. Script `scripts/fresh_names_study.py`, run ONCE on training; the held-out slice is opened once, only on a
training PASS.

## Why
Owner, 2026-11-16 night: "why are we still losing money ... the brain is supposed to kick in for each specific
respective ticker ... figure out exactly what's going on". The loss attribution of that night (PLAYBOOK section 5)
leaves one book with a positive tested average: the daily model's LONG calls (+0.25% per call on 3,448 calls, section
6b). Every caveat on that number is about selection:
- the 21 names were picked knowing they became giants (6 chosen by hand, 12 "unseen" large caps, TSLA, RDDT, AAOI);
- "long calls only" was itself chosen AFTER looking at those 21 records (the long-and-short model FAILED on the 12
  unseen names, `prereg_megacap_oos_2026-10-30`: Sharpe 0.44, lower bound below zero);
- five filter studies on the same 21 records all FAILED, so nothing has ever confirmed the long-call edge on data
  that did not suggest it.
This test asks the one question those caveats leave open: does the same model, with the same frozen parameters, make
the same kind of long call pay on stocks that were never looked at and were chosen without hindsight?

Priors, stated before the run: short-term reversal in large caps is real but small after 2010 (Nagel 2012; de Groot,
Huij and Zhou 2012) and the 21-name record has decayed (hundredths of a percent a day by year: +16.0, +7.0, -1.8, +6.6,
+3.5, +4.2, -13.5). A FAIL is at least as likely as a PASS. Either result changes what the long-calls paper book is
worth: a PASS supports widening it; a FAIL says its tested average is a selection effect until its own forward
ledger proves otherwise.

## Universe (fixed by this rule; no outcome was looked at)
Ranked on information available BEFORE the test window: every symbol in `data/daily` with at least 200 sessions in
2019, by median daily dollar volume (close x volume) in 2019, largest first. Removed, in this order:
1. exchange-traded funds and notes (SPY QQQ IWM EEM EFA GDX XLF HYG GLD IVV TLT FXI TQQQ LQD XLK XLU XLV EWZ XLI DIA VXX
   VOO XLE XLP IEMG SMH IYR GDXJ XOP VWO XLY XBI IEF AGG IEFA VNQ VTI EWJ EMB MDY UVXY SQQQ KRE XLB USO VEA, and any
   other fund met further down the list);
2. the 21 names already modelled (AAPL AMZN GOOGL MSFT META NVDA TSLA RDDT AVGO NFLX COST JPM V MA LLY UNH XOM WMT
   ORCL AMD AAOI) and their second listings (FB, GOOG);
3. names whose daily history ends before 2020-01-02 (nothing to score).
The first 80 names left are the universe (the list is written to `reports/fresh_names/universe.json` before any
price is fetched). It contains later losers on purpose (BA, INTC, ROKU, PYPL, BIIB, SNAP ...). A name is scored on the
sessions it has; a name with fewer than 1,000 sessions of 15-minute data is dropped and reported (acquired or
renamed names: a known, small survivorship effect in the model's favour).

## Model (unchanged) and the one data difference
`sgt.megacap.daily.build_daily` + `sgt.megacap.walkforward.walk_forward` with the frozen `megacap_daily_study.PARAMS`,
`DAILY_FEATURES`, monthly expanding window from 2018-01, 1-session embargo, long call = forecast at or above the
90th percentile of the in-sample forecasts. Labels and daily features from split- and dividend-adjusted daily bars
(`megacap_study.adjusted_daily`).
The 21 names used 1-minute bars (decision data ending 15:49 ET). Fetching 1-minute history for 80 names takes about
15 hours, so the fresh names use 15-MINUTE bars (SIP, raw): each bar is laid on the 1-minute grid as its open for
its first 14 minutes and its full open/high/low/close in its last minute, so everything read at the 15:49 cutoff is
a price printed by 15:45 (the open of the 15:45 bar and the bars that ended by then). SPY and QQQ are aggregated from
the local 1-minute store the same way. The four same-day features (gap, open to cutoff, last 60 minutes, range) are
therefore measured at 15:45 instead of 15:49, for training and for testing alike.
Bridge (descriptive, no gate, run after the verdict): the 21 known names rebuilt from 15-minute bars and compared
with their stored 1-minute records (forecast correlation, share of long calls in common, mean long call).

## Windows
Training 2020-01-02 .. 2026-06-30. Held-out 2026-07-01 .. 2026-10-28 (the same dates as the sibling tests; for the
fresh names both are unseen, the split only keeps a second, later look in reserve). Cost 4 bp per call.

## Decision (training, both must hold)
- **D1, the book makes money.** Equal-weight long-calls ledger as `sgt.megacap.longonly`: per session, the sum of
  (next close-to-close return - cost) over the long calls, divided by the number of names with a row that day.
  Mean per session > 0 with the one-sided session-bootstrap lower bound above 0 at alpha 0.025.
- **D2, the calls are skill, not just being long.** Per session with at least one long call: mean return of the long
  calls minus the mean return of ALL fresh names that day. Mean > 0 with one-sided session-bootstrap p < 0.025.
Bootstrap: 10,000 resamples of sessions, seed 20261117, p = (1 + resamples <= 0) / (n + 1) (fail closed). Two tests at
0.025 each (Bonferroni for 0.05). Required size: at least 60 names scored and at least 5,000 long calls; otherwise NO
VERDICT, counted as FAIL.
Held-out (only on a PASS, once): CONSISTENT when both means are above 0, NOT CONSISTENT otherwise (too short for a
significance test: about 80 sessions).

## Reported, not decided
Long calls: n, mean, win rate, worst five. Ledger: Sharpe, total, largest drawdown, worst day, by year, 2025 to June
2026 alone. The same at twice the cost. The sibling tests' sized book (weight min(1% / 21-day volatility, 25%),
100% gross cap). Share of names whose long calls beat their own average day. Holding all fresh names equal weight.
Next to each, the 21-name record's number.

## What follows
- PASS (and held-out CONSISTENT): proposal to the owner to widen the long-calls PAPER book to these names; owner
  decides; forward ledger and stop rule as `prereg_megacap_long_forward_2026-11-13`.
- FAIL: no book changes by this file. The model long call's score keeps its tested average but is labelled
  "not confirmed on unseen stocks"; the owner is told plainly; the forward ledger stays the only judge.
- Either way: one run. The script refuses a second training run; no variant of the universe, cutoff, threshold or
  parameters is tried afterwards on these names.

## Known limits
Today's S&P-style survivorship is avoided by ranking on 2019 data, but a name that stopped trading has fewer rows.
15:45 instead of 15:49 inputs. One cost for all names (the fresh names are the 80 most traded US stocks of 2019;
their spreads are a few basis points). No borrow question: long only.

## Result (2026-11-17, single training run; `reports/fresh_names/result_train.json`)
Plan frozen at `8f06257`, code `68b5079` (fetch plumbing fixed in `e5ac589` before any bar of a full-history name was
stored: the bar API pages on the underlying minutes, so the first 60-page cap cut every name but one; no decision
logic changed). 80 names fetched, none dropped (shortest histories: RTN 1,070 sessions, AGN 1,095, XLNX 1,540).

**VERDICT: FAIL.** D1 holds, D2 does not; both were required.

| | fresh 80 names | the 21 names (for scale) |
|---|---|---|
| sessions / long calls | 1,631 / 13,542 | 1,631 / 3,448 |
| a long call, after cost | +0.152%, 51.8% won | +0.25%, 53% won |
| an ordinary day of the same names | +0.057% | +0.13% |
| **D1** equal-weight ledger, mean per session | **+0.0166%**, lower bound +0.0009%, p 0.019 (needs < 0.025: holds, barely) | +0.027% |
| **D2** calls minus all names that day, mean per session | **+0.036%**, lower bound -0.012%, **p 0.069** (needs < 0.025: FAILS) | not measured |
| ledger: Sharpe / total / largest drawdown / worst day | 0.79 / +29.9% / -5.2% / -2.1% | 0.95 / +53.2% / -5.6% / -2.5% |
| ledger at twice the cost | Sharpe 0.59, +21.1% | - |
| sized like the live long-calls book (1% / volatility, 25% cap) | +53.1%, Sharpe 0.40, drawdown -38.8% | +82.6%, Sharpe 0.57, -22.3% |
| simply holding the names, equal weight | +123.2%, Sharpe 0.69, drawdown -36.2% | (about +35% a year) |
| names whose calls beat their own average day | 60 of 78 | 18 of 21 |

Ledger by year, hundredths of a percent a day: +4.3 (2020), +1.3, +0.7 (2022), +0.2, +0.6, +2.8 (2025), +1.9
(January to June 2026); no losing year, 2025 onward +2.5. Worst calls: XYZ 2020-03-13 -28.6%, MPC 2020-03-11 -27.0%,
DAL 2020-03-17 -26.0%. Best names by call excess: MU, LRCX, AMAT, SHOP, CAT; worst: MPC, DAL, TWLO, BA.

Reading, in plain words:
- The idea is not empty: on stocks it never saw and that were not picked for having done well, a long call made
  +0.15% against +0.06% for an ordinary day, three calls in four names beat their own average day, and no year lost.
- It is smaller than the 21-name number: +0.15% against +0.25% per call after cost, or +0.19% against +0.26% before
  cost on equal terms (the bridge below). Between a quarter and 40% of the tested average was the choice of names.
- What the model adds is mostly WHEN, not WHICH. Pooled, a call beats an ordinary day by about +0.13%; on the same
  day against the other 79 stocks it beats them by only +0.036%, not distinguishable from zero at the frozen level.
  The calls bunch on days after falls, when every stock tends to bounce: the model is a dip-buying timer for the
  group more than a stock picker (the same thing the market-filter test showed from the other side).
- As a way to hold stocks it does not beat holding them: the live sizing on these names made +53% with a -39%
  drawdown, holding made +123% with -36%. Per dollar actually at risk it earns more (it is invested about a tenth
  of the time in each name), which is the only sense in which it "works".
- Held-out stays closed (no PASS). No variant is tried on these names.

Consequence (as frozen): no book change. The model long call keeps its tested average on the site with the label
"not confirmed on unseen stocks" (`sgt.ops.score`). The forward ledger of the long-calls paper book stays the only
judge, with its -5% stop.

## Bridge (descriptive, `reports/fresh_names/bridge.json`): is the quarter-hour variant the same model?
The known names rebuilt from quarter-hour bars against their stored 1-minute records, training window (20 names:
RDDT has fewer than 1,000 sessions and is dropped by the same rule). Forecast correlation 0.92; 76% of the 1-minute
long calls are also quarter-hour long calls (3,416 and 3,391 calls); mean long call before cost +0.297% (1-minute)
and +0.260% (quarter-hour); equal-weight ledger after cost +0.027% a day, Sharpe 0.95 (1-minute) and +0.024%,
Sharpe 0.80 (quarter-hour). So the variant is a close but slightly weaker copy (about an eighth less per call), and
on equal terms the comparison is +0.19% per call on fresh names against +0.26% on the known ones. The 15:45 inputs
do not explain the FAIL: D2 is a same-day comparison inside the fresh names.
