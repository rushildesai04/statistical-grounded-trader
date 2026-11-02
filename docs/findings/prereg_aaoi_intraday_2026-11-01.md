# Pre-registration: AAOI + high-beta peers, intraday day-trading rules (2026-11-01)

Committed BEFORE any 1-minute bar from the held-out period (2024-01-02 .. 2026-10-30) is examined
for these rules. Source of the ideas: the user's trader (AAOI/AAOX/AAOZ are "most likely to make
money"; high beta; morning money and power hour matter; institutions keep buying, which matters most).

## Universe (fixed before any analysis)
AAOI and seven high-beta optical/AI-hardware peers: LITE, COHR, CIEN, FN, SMCI, MRVL, CRDO.
(COHR before 2022-07 is the pre-merger Coherent Inc; kept as listed.) Alpaca SIP 1-minute bars,
regular hours. AAOX/AAOZ have too little history (since 2026-03 / 2026-07) to test on their own.

## What was looked at already (training 2016-01-04 .. 2023-12-29, exploration only)
- Beta (all years, descriptive; beta is a risk measure, not a return signal): AAOI 1.33 (2016-23),
  3.86 (2024-26), 5.19 (last 60 days); R-squared vs SPY 0.21, i.e. 79% of AAOI's daily variance is
  stock-specific. Peers 2.0-2.9 since 2024.
- Time of day, AAOI training: volume is U-shaped (first half hour 15.0% of the day, last 16.4%), but
  direction is not: 49% of first half hours up (mean +0.09%, sd 2.5%), last half hour 43% up
  (mean -0.06%). First-30-minute direction held to the close: +0.09%/day net, 47% win. Power hour
  in the 09:30-15:00 direction: -0.13%/day.
- 5-minute opening-range breakout, pooled 8 names: gross +0.005%/trade, net -0.14%/trade (RV >= 1),
  negative on 7/8 names. VWAP trend (re-checked every 1/5/15/30/60 min): gross +0.006% .. +0.045%/day,
  net negative on 8/8 names at every interval.
No training cell passes, so no rule DERIVED from this exploration is tested further.

## The one rule that is tested on held-out: the published ORB (not derived from our data)
Zarattini, Barbon & Aziz, "A Profitable Day Trading Strategy For The U.S. Equity Market"
(SSRN 4729284, Feb 2024; sample 2016-2023). Our held-out period is post-publication. Rules exactly as
published, per stock and session (implemented in `scripts/aaoi_study.py::orb_trades`):
- Filters known at the open: open > $5; 14-day average volume >= 1,000,000 shares; 14-day ATR >
  $0.50; relative volume of the 09:30-09:35 range vs its 14-day average >= 1.0. (The paper's
  "top 20 by relative volume" across all US stocks cannot apply to 8 names and is dropped.)
- Direction of the first 5-minute candle (doji: no trade); stop entry at its high (long) / low
  (short); stop loss 10% of ATR14 from entry; otherwise exit at the 15:59 bar close.
- Fills: entry at the level or the bar open if gapped through; stop at the level, or the bar open if
  gapped through; entry and stop inside one bar = stopped (resolved against us). Raw prices; the 14
  sessions after an apparent split are skipped.
- Costs: $0.0035/share + 5 bp per side.

## Tests (family of two, alpha 0.05/2 each, one-sided)
- H1 pooled: all 8 names, trades pooled. H2 AAOI alone.
- Statistic: mean net return per trade; lower bound by session-clustered bootstrap (10,000 draws,
  seed 20261101) at the 2.5% quantile.
- PASS (per test) requires: n >= 100; lower bound > 0; mean net > 0 in both halves of held-out
  (by time); share of total net from the 5 best trades < 50%.
- Secondary, reported only: mean R; long vs short; relative volume >= 2; per name; AAOX as the
  vehicle for AAOI long signals on days AAOX traded (2026-03-24 ..); costs doubled.

## What the verdict means
PASS -> forward paper test of that rule on Fly (no capital), then a position-sizing design using
beta and ATR. FAIL -> the morning-breakout day-trading idea is rejected for these names.
Trial ledger run id: `aaoi-intraday-2026-11-01`.

## Result (2026-11-01, run once, commit 9d277c9, trial ledger `aaoi-intraday-2026-11-01`) — VERDICT: FAIL (both)

| Held-out 2024-01 .. 2026-10 | n | mean net/trade | gross | mean R | win | lower bound | halves | top-5 share |
|---|---|---|---|---|---|---|---|---|
| H1 pooled (7 names trade; FN never passes the volume filter) | 1,423 | -0.055% | +0.057% | -0.18 | 14% | -0.19% | +0.01% / -0.12% | n/a (total < 0) |
| H2 AAOI | 212 | +0.124% | +0.255% | +0.10 | 16% | -0.31% | +0.23% / +0.02% | 2.79 |

AAOI's positive mean comes from a handful of trend days: the 5 best trades are 279% of its total
(without them it loses), 84% of trades lose, and 2026 to date is -0.11%/trade. Secondary: long
+0.04%, short -0.16%, relative volume >= 2 -0.04% (402), costs doubled -0.17%; per name AAOI +0.12%,
CRDO +0.19%, SMCI +0.12%, MRVL -0.11%, CIEN -0.17%, LITE -0.24%, COHR -0.32%. AAOI signals traded
through AAOX/AAOZ (2x, 2026 only): 38 trades, +1.84%/trade (too few to mean anything).
Reading: the published morning breakout did not survive publication on these names; on AAOI it is
a lottery ticket (rare big trend days), not a steady edge.
