# Forward record: the frozen intraday trend ("noise area") rule on SPY and QQQ (2026-11-19)

Status: FROZEN before the recorder has scored any session. Owner, 2026-11-19, asked whether to keep a free forward
record of the rule that failed its test on SPY and looked good on QQQ: "Record forward".

## Why
`prereg_intraday_momentum_2026-11-19`: the published rule (long or short at half-hour marks when price leaves a
volatility band around the open, VWAP trailing stop) earned nothing on SPY out of sample (2024-05 to 2026-10: -2.3%
in total, Sharpe -0.13): FAIL. QQQ under the same rule made +26.8% (Sharpe 1.12), but QQQ was a robustness check by
that plan and is not a pass. A result seen once, on an instrument that was not the registered one, can only be
confirmed on sessions nobody has seen. This record is that: it costs nothing and trades nothing.

## The rule (not changed)
`scripts/noise_area_study.py` exactly as it was scored: rule B, next-bar fills, careful costs ($0.02 a share each
side), full size, official (SIP) 1-minute bars, boundaries from the previous 14 sessions. No parameter is touched.

## The record
- Instruments: SPY and QQQ, each its own ledger line per session.
- Sessions: every completed regular session from 2026-11-20 on (the first session after the owner's word).
- Each night after the official bars exist, `scripts/noise_area_forward.py record` scores the sessions not yet in
  `reports/noise_area_forward/ledger.csv` and appends them. A session is scored once and never rewritten.
- No order, no text. One line in the nightly summary (sessions, sum and average of the daily net return per
  instrument), under the record lines, never in a total.

## Decision (once)
On the first night both instruments have 250 recorded sessions (about one year), on each instrument's first 250:
the mean daily net return and its one-sided lower bound (the study's own block bootstrap, 10,000 resamples, seed
20261119) at alpha = 0.05 / 2 (two instruments).
- PASS for an instrument = mean above 0 and lower bound above 0. Put to the owner as a candidate paper book.
- Anything else = closed for that instrument; not tried again in another form on this data.
The result is written to `reports/noise_area_forward/result.json` once and is never recomputed.

## Known limits
The running numbers are shown every night; the decision is a fixed-count test, so looking does not change it, but
nothing is concluded before the count. One year of sessions cannot settle a rule whose yearly result swings by
tens of percent; a PASS here is a reason for a paper book, not for money.
