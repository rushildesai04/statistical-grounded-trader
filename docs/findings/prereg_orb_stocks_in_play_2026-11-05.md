# Pre-registration: opening-range breakout on stocks in play, a faithful replication (2026-11-05)

Committed BEFORE any data for this study is downloaded or any return computed. Vault study
`orb-sip-2026-11` (config/holdout_registry.json): training 2021-01-04 .. 2023-12-29, held-out
2024-01-02 .. 2026-10-30.

## Why
The owner asked for the price/volume signals day traders actually use, backed by research. The strongest published
evidence for a minute-level price/volume rule is Zarattini, Barbon and Aziz (2024), "A Profitable Day Trading
Strategy For The U.S. Equity Market" (Swiss Finance Institute RP 24-98): a 5-minute opening-range breakout on the
day's top 20 "stocks in play" by relative volume, reporting 41.6%/yr and Sharpe 2.81 net of commissions over
2016-2023. Our earlier ORB test (orb_2y, 2026-10-18) was NOT this rule: sub-$5 gappers, long only, stop at the range
low, no relative-volume ranking. Practitioner critiques say slippage at the open erases much of the edge; the paper
models commissions only. The paper's sample ends 2023, so 2024-2026 is data its authors never saw.

## Rule (fixed; ambiguities in the public summaries resolved here, before any data)
Universe on day D: Alpaca US equities (active and inactive assets), NYSE / NASDAQ / AMEX, using only data before D's
open except the opening range itself.
- Filters: D's official open > $5; mean daily volume of the prior 14 sessions >= 1,000,000 shares; 14-day ATR
  (Wilder, from split-adjusted daily bars, prior 14 sessions) > $0.50.
- Relative volume: volume of D's first five 1-minute RTH bars (09:30-09:34) / mean of the same five-bar volume over
  the prior 14 sessions (sessions with no bars count as 0; a name needs >= 10 of 14 sessions with bars). RVOL >= 1.
- Stocks in play: the 20 highest RVOL names passing the filters, ranked at 09:35.
- Direction: opening range = bars 09:30-09:34. Close of 09:34 > open of 09:30 -> long only; < -> short only; equal ->
  no trade.
- Entry: a stop order at the range high (long) or low (short), live from 09:35 to 15:59. It fills on the first
  1-minute bar whose high >= range high (long) at max(range high, that bar's open); short mirrored. One trade per
  stock per day.
- Stop: entry -/+ 0.10 x ATR14. Checked from the entry bar on; in the entry bar itself the stop counts as hit if the
  bar's low (long) / high (short) crosses it (worst case). Fill at the stop, or at a later bar's open if it gapped
  through.
- Exit: close of the last regular-session bar (15:59) if not stopped.
- Data: Alpaca SIP 1-minute bars (raw prices intraday; split-adjusted daily bars for ADV and ATR).

## Costs
- Primary: commission $0.0035/share per side (the paper's) PLUS the measured half-spread per side from
  `GAPPER_COST_MODEL` (5%-trimmed RTH NBBO on gap days, with the open-hour multiplier), priced at entry. This is
  deliberately conservative: stocks in play at the open are exactly the wide-spread case the critique points at.
- Secondary (reported only): commission only (the paper's assumption); half the measured spread.
- Shorts assume shares are borrowable at no fee (liquid > $5 names held intraday) [VERIFY on fills].

## Metric and gates
Per-trade net return on notional, by direction and pooled.
- Development (training 2021-2023, run once): REPORT ONLY. It checks the replication: if the commission-only gross
  is not clearly positive, the replication is broken and the held-out is NOT opened (fix, then re-run training).
- Held-out (decisive, run once after the vault unlock): PASS needs all of: >= 200 trades; primary mean net > 0 with
  a day-cluster bootstrap one-sided 95% lower bound > 0 (10,000 resamples, seed 20261105); both calendar halves
  positive (split 2025-06-01); no single month > 50% of the total.
- PASS -> paper forward on the runner (signals texted at 09:35, entries/exits simulated from live data), n >= 300
  forward trades before any money, and live slippage measured against this cost model. FAIL -> written up, nothing
  deployed.

## Secondary (reported only)
Long vs short; top 5 / 10 / 20; 15-minute range; portfolio simulation with the paper's sizing (1% of capital at risk
per trade, 4x leverage cap, $25,000 start); hit rate; share stopped; per year.
Trial ledger run ids `orb-sip-2026-11-05-dev`, `orb-sip-2026-11-05-holdout`.

## Known limits (stated now)
Survivorship: Alpaca's inactive-asset list may miss some delisted names. ETFs are not excluded by asset type
(Alpaca has no such flag); leveraged/sector ETFs rarely top RVOL at the open but may appear. Both are reported.

## Amendment A1 (2026-11-05, during implementation review; before the full training run, held-out sealed)
- Code review found the stop measured from the trigger price instead of the executed entry (this file says
  "entry -/+ 0.10 x ATR14"). Fixed to match the text, with a regression test. No rule change.
- The worst-case check inside the entry bar (a 1-minute bar's range often exceeds 0.10 x daily ATR) may by itself
  decide the replication. Added as SECONDARY (reported, never gating): the same trades with the entry-bar stop check
  skipped, commission only, to bound the intrabar ambiguity. The primary stays worst case.
- During implementation the `dev` command was run on a 19-session slice (2021-02) to validate the pipeline; it is not
  the training result and is superseded by the full 2021-2023 run.
