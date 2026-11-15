# Pre-registration: two risk rules for the daily model's long calls: earnings skip and crowded-day cap (2026-11-15)

Status: FROZEN before any number below is computed. Script `scripts/long_rules_study.py`, run ONCE on training; the
held-out slice is opened once per rule, only on that rule's training PASS. Third study on this record after
`prereg_money_score_2026-11-14.md` and `prereg_record_score_2026-11-14.md` (both FAIL).

## Why
The 2026-11-13 review of every book (PLAYBOOK section 5) named two faults of the daily model that cost money that day:
it is blind to events (it has only price and volume), and it is blind to overlap (AMD and AAOI long together on a chip
selloff). Two fixes were listed as "test first". The long-calls paper book (`megacap_long`) starts 2026-11-16 and
Nvidia reports on 2026-11-17 after the close, so the question is live this week.

Priors from the literature, stated before the run so a FAIL is not a surprise:
- Stocks earn MORE than average around their earnings dates (the earnings announcement premium: Frazzini and Lamont
  2007; Barber et al. 2013; Savor and Wilson 2016), with more risk. Skipping earnings is therefore a risk rule, and may
  cost return.
- Cutting exposure when predicted volatility is high improved Sharpe in-sample (Moreira and Muir 2017) but mostly did
  not hold out-of-sample or after costs (Cederburg et al. 2020; Barroso and Detzel 2021). The prior for the cap is weak.

## Data
As the two sibling tests: the walk-forward out-of-sample records of the 21 names (`reports/megacap_daily_v2`,
`reports/megacap_oos`, `reports/aaoi_daily`); training 2020-01-02 .. 2026-06-30, held-out 2026-07-01 .. 2026-10-28.
Long calls only (position +1; short calls and flat rows hold nothing). Net per call = next close-to-close return -
cost (4 bp, RDDT 10 bp). Earnings dates: SEC EDGAR 8-K item 2.02 acceptance times in `data/earnings/raw_<SYM>.parquet`.
RDDT and AAOI have no file and XOM has none before 2026-07: for those three names rule E never skips (stated, not
hidden: the rule covers 18 of 21 names). Honest note: the pooled long record (n 3,448, +0.25% per call) has been seen;
nothing below has been computed.

## The base book (BASE)
The sizing of the sibling test: weight of a long call = min(1% / d_rv21, 25%), all weights of a day scaled down
together when they sum above 100% (`sgt.megacap.score.base_weight`, `score.book`).

## Rule E: no new long into the stock's own earnings
A long call on stock i decided on session D is SKIPPED (weight 0, the freed weight is not given to other calls) when
an 8-K item 2.02 of stock i was accepted at or after 16:00 ET on D and before 16:00 ET on the next session: the
release lands inside the close-to-close hold. A release accepted before 16:00 on D is already in the model's inputs
and is not skipped. Early closes are treated as 16:00 (the few half days can only miss a flag, never add one).

## Rule V: crowded-day cap
For a session D with long calls, with the BASE weights w of that day:
- predicted daily swing of the book `s_D = sqrt(w' C w)`, C = sample covariance of the called names' close-to-close
  returns over the 60 sessions ending two sessions before D (the return ending at D's own close is not known at the
  15:50 decision). A pair with fewer than 40 common sessions gets covariance 0; a name with fewer than 40 gets
  variance d_rv21 squared.
- cap `c_D` = the median of `s` over all earlier sessions that had a long call; no cap until there are 60 of them.
- every weight of the day is multiplied by `min(1, c_D / s_D)`.
So a day whose calls move together (same theme) or are all volatile is cut back to the typical day's risk; nothing is
ever raised.

## Tests (one-sided, session-clustered bootstrap, 10,000 resamples, seed 20261115; Bonferroni 0.05 / 2 = 0.025 each;
## p = (1 + resamples with statistic <= 0) / 10,001: fail closed)
- T-E: per session, `ret_E - ret_BASE` (a session with no skipped call contributes 0). Statistic = its mean over all
  training sessions. Positive = the skipped calls lost money, so skipping paid. No verdict (FAIL) with fewer than 30
  skipped calls in training (8 in held-out).
- T-V: per session, `k x ret_V - ret_BASE`, k = average gross of BASE / average gross of V over the window (same
  average money at work; k is computed on the window being tested). Statistic = its mean. Positive = moving money
  from crowded days to ordinary days paid. No verdict (FAIL) when the cap binds on fewer than 100 training sessions.
- PASS (per rule): statistic > 0 and p < 0.025 on training AND on held-out.
- Reported, never deciding: skipped calls against kept calls (count, mean net, standard deviation, win rate, worst
  five); per-year means; for each book the mean per day, Sharpe, largest drawdown, worst day and worst 5-session
  stretch; how often the cap binds and the average cut.

## Decision rule
- A rule that PASSES is proposed to the owner as an amendment to the long-calls book's sizing
  (`prereg_megacap_long_forward_2026-11-13.md`), dated before the first session it applies to. Nothing changes
  without the owner's OK.
- A rule that FAILS changes nothing. Its reported risk numbers are shown to the owner as information only (what a
  long call held through earnings has looked like), with the plain statement that the test failed.
- Whatever the result: no real money, no change to the two long-and-short forward tests, to the deciding ledger of
  the long-calls test, or to any alert book.
