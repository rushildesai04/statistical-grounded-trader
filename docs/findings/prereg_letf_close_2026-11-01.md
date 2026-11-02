# Pre-registration: leveraged-ETF rebalancing -> last-half-hour continuation (2026-11-01)

Committed BEFORE any last-half-hour return is computed for these stocks under this rule.

## Mechanism (why this, from the trader's AAOX/AAOZ + power hour + "institutions buying")
A daily-reset 2x long fund must BUY its stock near the close on up days and SELL on down days
(hedge = L(L-1) x assets x day's return: 2 x for a 2x long fund, 6 x for a 2x inverse fund, same
direction). Index-level evidence: Cheng & Madhavan (2009); Baltussen, Da, Lammers & Martens (2021,
JFE, "Hedging demand and market intraday momentum"). Single-stock 2x funds are new (2022+), so the
same forced flow may push single stocks in the direction of the day's move into the close.
Prediction: after a stock's first 2x fund launches, the last half hour continues the day's move,
more than it did before launch.

## Universe and launch dates (first Alpaca daily bar of the earliest unambiguous 2x fund)
TSLA 2022-08-09 (TSLL), NVDA 2022-12-13 (NVDL), COIN 2022-08-09 (CONL), AAPL 2022-08-09 (AAPU),
AMZN 2022-09-07 (AMZU), GOOGL 2022-09-07 (GGLL), MSFT 2022-09-07 (MSFU), AMD 2024-03-18 (AMDL),
META 2024-06-05 (METU), SMCI 2024-08-22 (SMCX), AVGO 2024-08-22 (AVGX), MSTR 2024-09-18 (MSTU),
NFLX 2024-10-03 (NFXL), MU 2024-10-10 (MUU), PLTR 2024-12-11 (PLTU), AAOI 2026-03-24 (AAOX).
(Tickers NVDQ, MSTX, CONX carry older unrelated history on Alpaca and are not used for dates.)
Post-launch window starts 20 sessions after launch; ends 2026-10-30. Pre-launch: 2016-01-04 (or
listing) to launch - 1.

## Rule (per stock, per full regular session; 1-minute SIP bars, raw prices)
- r = open of the 15:30 bar / close of the previous session's 15:59 bar - 1.
- Trade only if |r| >= sd20, the standard deviation of the previous 20 sessions' 15:59-to-15:59
  returns (known before the session).
- Position sign(r) at the 15:30 bar open; exit at the 15:59 bar close.
- Costs: $0.0035/share + 5 bp per side. Sessions within 20 days after an apparent split
  (|overnight ratio| outside 0.4..2.5) and early-close sessions are skipped.

## Tests (family of two; alpha 0.05/2; session-clustered bootstrap, 10,000 draws, seed 20261102)
- L1 (tradeable): post-launch trades pooled over all 16 stocks. PASS needs n >= 300, mean net > 0,
  2.5% lower bound > 0, both halves (by time) > 0, top-5 trades < 50% of the total.
- L2 (mechanism): mean GROSS per trade post-launch minus pre-launch, pooled (same rule both
  periods). PASS needs the 2.5% lower bound of the difference > 0 (bootstrap within each period).
- Secondary only: per stock; exit at the official daily close (closing auction) instead of 15:59;
  costs at 2 bp; LETF intensity terciles (sum of the stock's 2x funds' 20-day dollar volume / the
  stock's 20-day dollar volume, known before the session); AAOI alone.

## What the verdict means
L1 and L2 PASS -> forward paper test on Fly (no capital), sized by beta/ATR. L2 only -> the flow is
real but too small to trade after costs. FAIL -> rejected.
Trial ledger run id: `letf-close-2026-11-01`.

## Result (2026-11-01, run once, commit 3ab4036, trial ledger `letf-close-2026-11-01`) — VERDICT: FAIL (L1 and L2)

| | n | gross/trade | net/trade | win | lower bound | halves |
|---|---|---|---|---|---|---|
| L1 post-launch, pooled 16 stocks | 2,628 | -0.001% | **-0.105%** | 42% | -0.148% | -0.12% / -0.09% |
| pre-launch, same rule | 6,221 | +0.020% | | | | |
| L2 difference post - pre (gross) | | **-0.021%** | | | -0.076% | |

Secondary: exit at the official close (closing auction) -0.105%; costs 2 bp/side -0.045%;
LETF intensity terciles (fund $ volume / stock $ volume, median 0.1% / 0.9% / 6.5%): gross +0.004% /
-0.014% / +0.006% -> no dose-response. Per stock post-launch gross: MSTR -0.10%, AAOI -0.10% (51),
MU +0.07%, AMD +0.04%, all others within +-0.04%.
Reading: whatever the 2x funds buy into the close is absorbed (market makers pre-hedge or the
closing auction takes it); there is no last-half-hour continuation to trade, before or after costs.
