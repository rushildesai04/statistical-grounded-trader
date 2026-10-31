# Pre-registration: daily per-ticker models on 12 unseen large caps (2026-10-30)

Committed BEFORE any 1-minute bar for these tickers is downloaded. Nothing below may change after scoring.

## Question
The daily per-ticker method (`prereg_megacap_daily_2026-10-30.md`, corrected run v2: +8.1%/yr,
Sharpe 0.74, FAIL on Sharpe >= 1) showed a timing edge on the six names it was developed on. Does the
same frozen method make money on large caps it has never seen? This is the out-of-sample test of the
"one brain per ticker" idea.

## Tickers (fixed now, chosen as large liquid US stocks listed since 2016, without any model output)
AVGO, NFLX, COST, JPM, V, MA, LLY, UNH, XOM, WMT, ORCL, AMD.

## Method (identical code and parameters, nothing tuned)
- Data: Alpaca SIP 1-minute bars 2016-01-04 .. 2026-10-29 (`scripts/backfill_alpaca.py`), adjusted
  daily bars (Alpaca, adjustment=all), SPY/QQQ context as before.
- Features `sgt.megacap.daily.DAILY_FEATURES`, `build_daily`; `walk_forward(start="2018-01",
  embargo_sessions=1)` with the v2 LightGBM PARAMS; 10th/90th-percentile thresholds; decision 15:50 ET,
  close-to-close next day; 4 bp round trip for every ticker; equal weight; portfolio day = mean.

## Verdict (computed once; script `scripts/megacap_oos_study.py`)
PASS (the edge generalizes) only if ALL: (1) day-clustered bootstrap lower95 of mean daily net > 0;
(2) mean daily net > 0 with costs doubled; (3) >= 6 of the 9 calendar years 2018-2026 positive.
Reported, not decisive: Sharpe, 2025+ mean, per-ticker results, and the 18-name portfolio (these 12 plus
the original 6) Sharpe.

## What happens next
PASS -> amend the forward test (before its next month starts) to add these names; deployment still
needs the forward evidence. FAIL -> the six-name edge is treated as likely selection luck, and the
forward test continues unchanged as the final arbiter.
