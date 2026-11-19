# Pre-registration: intraday momentum on SPY, the published "noise area" rule on data after its sample (2026-11-19)

Status: FROZEN by this commit, BEFORE any code for this study exists and before any return of SPY or QQQ is computed
under this rule. Bar COVERAGE was checked (2,723 sessions each, 2016-01-04 to 2026-10-30, none missing; 390 bars
on full days, 210 on half days); no price was read. Study id `intraday-momentum-2026-11` in the holdout vault
(`config/holdout_registry.json`): the scorer can read 2024-05-01 and later only after the vault confirms this
file and the scorer are committed unchanged, once. The draft was red-teamed before the freeze (adversarial pass,
2026-11-19); its five corrections are in this text.

## Why
Owner, 2026-11-19: "act on it ... find the best way to build solutions ... use the web as your resource ...
adversarially challenge everything". The alert long book wins 15.6% of its trades and needs 32.6% to break even; no
stop, target or size rule closes that (`long_review_2026-11-19.md`, adversarial pass of 2026-11-19). A long side that
earns needs a different source of edge. The one with published support and a cost our books can carry is intraday
trend in index funds: Gao, Han, Li, Zhou (Journal of Financial Economics 2018) and, as a tradeable rule, Zarattini,
Aziz, Barbon, "Beat the Market: An Effective Intraday Momentum Strategy for S&P500 ETF (SPY)" (Swiss Finance
Institute Research Paper 24-97; read in full for this plan). Their sample is May 2007 to April 2024. We hold SPY and
QQQ minute bars to 2026-10-30, so 2024-05-01 to 2026-10-30 is data the authors never saw: a true out-of-sample test.

Reasons to doubt it, stated before the run: the authors sell trading education; it is a backtest; a community re-run
reported far less; their base rule earned little from 2010 to 2017 (their Figure 3); published rules decay; our
faithful replication of the same authors' opening-range rule FAILED (`prereg_orb_stocks_in_play_2026-11-05`).
The prior for a pass is below one half.

## The rule (the paper's, pages 6 to 15; nothing tuned here)
Regular session only, 1-minute bars. A bar is named by its START; "the price at HH:MM" is the close of the bar that
ENDS at HH:MM (the paper's bars are stamped at their end).
1. For each of the previous 14 sessions i and each time HH:MM: move = |price at HH:MM / that session's 09:30 open - 1|.
   sigma(HH:MM) = the mean of those 14 moves. A time is used only when at least 10 of the 14 sessions have it
   (half days end early; this is the one detail the paper does not state).
2. Upper = max(today's 09:30 open, the previous session's last regular-session close) x (1 + sigma).
   Lower = min(same two) x (1 - sigma). (After a half day the previous close is that day's 13:00 close.)
3. VWAP = the session's running volume-weighted price from 09:30 (sum of bar VWAP x bar volume / sum of volume).
4. Decisions only at HH:00 and HH:30, from 10:00 to the last half-hour mark before the close. All out at the close.
5. Rule B (the paper's "current band + VWAP", its second line in Tables 2 to 4), the PRIMARY rule:
   flat or short, and price > max(Upper, VWAP): be long. Flat or long, and price < min(Lower, VWAP): be short.
   Long and price < max(Upper, VWAP): close (trailing stop). Short and price > min(Lower, VWAP): close.
   (The paper states the stop; entering only beyond the stop is my reading: an entry inside it would be stopped
   at once. This reading is frozen here and is not revisited.) A reversal pays the cost on both legs.
6. Rule A (the paper's base: exit only at the close or on a cross of the opposite boundary, then reverse): reported,
   never decided on.
7. Size: 100% of the book's value at the previous close, no leverage (shares = floor(value / today's open)). The
   paper's volatility-targeted version (up to 4x) is reported for comparison only: leverage is not tested here.
8. Fills: a decision at HH:MM is filled at the OPEN of the next bar (the bar starting at HH:MM), the close-out at
   the session's last close. Reported beside it: fills at the decision price itself (what the paper appears to do).
9. Costs per share, each side: PAPER = $0.0045 (its $0.0035 commission + $0.001 slippage); CAREFUL = $0.02 (a full
   cent of spread crossed plus a cent of slippage). The decision uses CAREFUL.

## Samples
- Replication period: 2016-01-25 (the first session with 14 before it) to 2024-04-30, inside the paper's sample.
- Out-of-sample: 2024-05-01 to 2026-10-30.

## What was already seen in the out-of-sample period (contamination statement)
Not clean-slate. In this repository the DAILY close-to-close returns of SPY and QQQ through 2026 have been
studied and reported by year (`prereg_dip_timer_2026-11-17`, `prereg_long_market_filter_2026-11-16`, the ETF
allocation books), and SPY / QQQ minute bars were context features of the large-cap models
(`prereg_fast_megacap_2026-11-03`, whose own held-out period was never unlocked). So the lead knows which way
these funds went, day by day, in 2024 to 2026. What has NOT been computed for either fund in any period: the
return from a half-hour mark to a later mark or to the close, a noise boundary, or any intraday rule's result.
This rule's daily result is measured from the open and takes both sides; it is close to uncorrelated with holding
the fund by the paper's own numbers (beta -0.03). The knowledge is disclosed, and the rule is the paper's with
nothing chosen here that this knowledge could steer.

## Step 1: the replication gate (replication period only; the out-of-sample stays closed)
The code is believed only if, for SPY under rule B with PAPER costs and decision-price fills (the paper's own
setting), BOTH hold on the replication period:
- trades per day between 1.3 and 2.3 (the paper's Table 4: 1.8; a property of the rule's mechanics, not of the
  market regime);
- the total return is positive (the paper's Figure 6 shows this rule rising through 2016 to 2024; a sign, side
  or cost error would show here; nothing can be tuned to it).
Outside either = the implementation or the data differs from the paper: STOP, report, the out-of-sample is not
opened, nothing is "fixed and retried" toward a pass. The share of profitable trades and days (paper: 37% and
43% over 2007 to 2024) is reported beside them but gates nothing: it depends on the period.
The replication period's own result decides nothing (the authors chose the rule on it).

## Step 2: the decision (out-of-sample, opened once, only after the gate)
ONE decision cell: SPY, rule B, next-bar fills, CAREFUL costs. The statistic: the daily net return of the book
(0 on a day without a trade). One test, alpha = 0.05, one-sided, bootstrap of the mean over sessions in blocks of
5 consecutive sessions (10,000 resamples, seed 20261119); no bound = fail.
- PASS: the lower bound of the mean daily net return is above 0.
- LEAD: the mean is above 0 but the bound is not (likely even if the paper's Sharpe of 1.2 held: about 630
  sessions give a t-statistic near 2, so the power is roughly 60%).
- FAIL: the mean is at or below 0.
QQQ is NOT a second chance: the paper is about SPY. QQQ under the same cell is reported as a robustness check and
cannot produce a pass on its own.
Reported beside the decision cell, deciding nothing, and labelled so in every write-up: annual return, volatility,
Sharpe, worst day, largest drawdown, share of profitable days and trades, trades per day, each half of the period,
each calendar year, longs and shorts apart, PAPER costs, decision-price fills, rule A, the volatility-targeted
size, QQQ, and the correlation with holding the fund.

## What follows
- PASS: put to the owner as a new PAPER book on the runner at probe size, counted toward the proof standard
  (300 live trades, interval above 0) like every other book. No real money on a backtest.
- LEAD: forward paper measurement only (signals recorded, no size), decided once after 250 further sessions.
- FAIL: recorded in the playbook; not tried again in another form.

## Known limits
One rule, one vendor's bars; the paper used IQFeed, we use the consolidated tape from Alpaca. 630 sessions cannot
confirm a small edge: a LEAD is the most likely honest outcome if the effect is real. The rule trades at
half-hour marks on the previous minute's close: live, the free feed is accurate on SPY and QQQ (within 0.05% in
94 to 99% of minutes on mega-caps, `prereg_long_feed_replay_2026-11-18`), but that was measured on stocks, not on
these two funds. Shorting an index fund is not the borrow problem small caps are, but it is still a short: the
owner's section-0 rules apply before any real money.

## Amendment A1 (2026-11-19, after the freeze, BEFORE any code of this study touched a real bar)
The contamination statement above is wrong in one place, found while writing the scorer: this repository already
holds a study of intraday momentum on these two funds. `prereg_anomalies_2026-10-31` (test A1, script
`scripts/intraday_momentum_study.py`) tested the rule of Gao, Han, Li and Zhou: the sign of the move from the
previous close to 10:00 decides a position from 15:30 to the close. Result on SPY, 2018-07-02 to 2026-10-29
(2,056 days): -0.021% a day after 1 basis point a side, 44% of days up, lower bound -0.037%, both halves negative,
QQQ the same: FAIL, "the published effect is gone after publication (gross about 0)".
So, corrected:
- The first half-hour's move and the last half-hour's return of SPY and QQQ HAVE been computed here, through
  2026-10-29, as one aggregate and two halves. No result was reported for 2024-05 onward alone, and no band, VWAP
  stop or half-hourly rule was ever computed.
- The prior for a pass is lower than the plan says: the paper names Gao et al. as the work it extends, and the
  part of this rule's result that comes from holding a morning trend into the close is the effect that failed
  here. The lead's prior for PASS: about one in five.
- Why the test is still run: the rule is materially different (it enters at any half-hour mark when price
  leaves a volatility band and leaves on a VWAP stop, and the paper claims it kept working through 2018 to 2024,
  the years in which our own test found the simpler effect gone). That claim is exactly what data after the
  paper can check, and the check is cheap.
- Nothing in the rule, the gate, the decision cell or the statistic changes. The scorer is
  `scripts/noise_area_study.py` (the earlier study's script keeps its name and is untouched); results go to
  `reports/noise_area/`.
- Process note: the lead offered this idea to the owner on 2026-11-19 without naming the earlier failed test,
  against the standing rule to search `docs/findings` before calling an idea untested. The search used the
  rule's name and missed a file named after "anomalies".
- The previous commit's message names this amendment, but a failed step left it out of that commit; it is added
  here, still before any run.

