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
