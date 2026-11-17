# Pre-registration: a market filter for the daily model's long calls (2026-11-16)

Status: FROZEN before any number below is computed. Script `scripts/long_market_filter_study.py`, run ONCE on
training; the held-out slice is opened once, only on a training PASS. Fourth study on this record after
`prereg_money_score_2026-11-14.md`, `prereg_record_score_2026-11-14.md` and `prereg_long_rules_2026-11-15.md`
(rules E and V); all FAIL so far.

## Why
Owner, 2026-11-16: "we lost most of the alpha model trades performed today ... see what we can make better". That day
the market fell (SPY -2.1%, QQQ -2.8%, semiconductors -5.2%) and every long position of the model books lost. The
review (PLAYBOOK section 6b) found the main cause was operational (stale positions; fixed), and one open question
about the model itself: it has only each stock's own price and volume, so it buys dips in a falling market exactly as
in a rising one. The one untested idea left on the list is a market filter on NEW long calls.

Priors, stated before the run so a FAIL is not a surprise:
- Trend filters on an index (price against its 200-day average: Faber 2007; time-series momentum: Moskowitz, Ooi
  and Pedersen 2012) have cut drawdowns of buy-and-hold over long histories, mostly by avoiding long bear markets.
- This book is not buy-and-hold: every call is a ONE-day hold picked by a model that leans on short-term reversal.
  Next-day returns below the trend are more volatile, not reliably lower: the largest up days of an index come in
  falling markets, and short-term reversal earns most when volatility is high (Nagel 2012). The filter may therefore
  remove the model's best days. The prior for a PASS is weak.

Honest note on what has been seen: the BASE book's mean return per year was printed in the previous study (2022 -1.8
and January to June 2026 -13.5 hundredths of a percent a day; every other year positive). That is what suggested the
rule. Whether those periods were below the trend as defined here, and every number of the rule itself, have not been
computed or looked at.

## Data
As the three sibling tests: the walk-forward out-of-sample records of the 21 names (`reports/megacap_daily_v2`,
`reports/megacap_oos`, `reports/aaoi_daily`); training 2020-01-02 .. 2026-06-30, held-out 2026-07-01 .. 2026-10-28.
Long calls only (position +1). Net per call = next close-to-close return - cost (4 bp, RDDT 10 bp). The BASE book is
the sibling tests' book: weight of a long call = min(1% / d_rv21, 25%), all weights of a day scaled down together
when they sum above 100% (`sgt.megacap.score.base_weight`, `score.book`).
Market: SPY daily closes from `data/daily/SPY.parquet` (2016-01-04 .. 2026-10-30; the chart price, not dividend
adjusted, as the 200-day average is conventionally drawn).

## Rule M: no new long call below the market's 200-day average
For a decision session D (the call is made at 15:50 on D and held from D's close to the next close):
- `below_D` is true when the SPY close of the session before D is lower than the average of the 200 SPY closes ending
  at that session. Every input is a close before D: D's own close is not known at 15:50.
- With fewer than 200 earlier closes, or no SPY close for the session before D, `below_D` is false (the book trades).
- On a session with `below_D` true every long weight is 0: no new long call is taken. Nothing else changes; a call
  of the day before ends at D's close as always.

## Test (one-sided, session-clustered bootstrap, 10,000 resamples, seed 20261116; p = (1 + resamples with
## statistic <= 0) / 10,001: fail closed)
- T-M: per session, `ret_M - ret_BASE` (0 on a session that is not below; minus the BASE return on one that is).
  Statistic = its mean over all sessions of the window. Positive = the long calls taken below the trend lost money,
  so not taking them paid.
- Significance level 0.05 / 3 = 0.0167: M is the third skip rule tried on this ledger (after E and V).
- No verdict (FAIL) with fewer than 100 below sessions that had at least one long call in training (20 in held-out).
- PASS: statistic > 0 and p < 0.0167 on training AND on held-out.
- Reported, never deciding: how many sessions are below and when (per year); the BASE book's mean per day on below
  sessions against the others; the long calls skipped against those kept (count, mean net, win rate); for BASE and M
  the mean per day, Sharpe, largest drawdown, worst day, worst 5-session stretch and per-year means.

## Decision rule
- PASS: proposed to the owner as an amendment to the long-calls book (`prereg_megacap_long_forward_2026-11-13.md`),
  dated before the first session it applies to. Nothing changes without the owner's OK.
- FAIL: nothing changes. The reported numbers are shown to the owner as information, with the plain statement that
  the test failed. The idea is then closed: not re-run with another average length, index or threshold.
- Whatever the result: no real money, no change to the two long-and-short forward tests, to the deciding ledger of
  the long-calls test, or to any alert book.

## Result (2026-11-16, training run once at code `5cde177`; `reports/long_market_filter/result_train.json`): FAIL
Training: 1,631 sessions, 3,448 long calls. 333 sessions were below the 200-day average (260 of them had a long
call): 59 in 2020, 203 in 2022, 17 in 2023, 42 in 2025, 12 in 2026, none in 2021 and 2024.

| | BASE | with rule M |
|---|---|---|
| mean per day | +0.045% | +0.005% |
| Sharpe | 0.57 | 0.09 |
| total over the window | +82.6% | +2.2% |
| largest drawdown | -22.3% | -30.3% |
| worst day | -10.1% | -4.7% |

T-M: mean difference -0.040% a day (lower bound -0.090%), p 0.96 against the 0.0167 needed: FAIL, and in the
opposite direction.

Reading:
- The calls the rule would skip are the model's best. 1,139 long calls made below the trend: +0.64% per call, 56%
  won. 2,309 made above it: +0.06% per call, 52% won. The BASE book earned +0.195% a day on below-trend sessions and
  +0.006% a day on all the others: almost all of the record's profit was made while the market was below its
  200-day average. This is the short-term-reversal prior stated above, and stronger than expected.
- The filter only buys a smaller worst day (-4.7% against -10.1%), and pays for it with nearly the whole return and
  a deeper drawdown (the losing stretches were above the trend, where the rule keeps trading).
- The two losing periods that suggested the rule were not below-trend periods in the way assumed: 2022 had 203 such
  sessions and the book made money on them (with the rule 2022 goes from -1.8 to -8.1 hundredths of a percent a
  day); January to June 2026 had only 12.
- Per the frozen decision rule nothing changes and the idea is closed: no other average length, index or threshold
  is tried. Held-out stays closed.
- What it says about the model, as information: its long calls are dip-buying that has paid in falling markets and
  about broken even in rising ones. A down day like 2026-11-16 is the kind of day after which, historically, its
  calls did best, not worst. That is an average over six and a half years, not a forecast for any one day.
