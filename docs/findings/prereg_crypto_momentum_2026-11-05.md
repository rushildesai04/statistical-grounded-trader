# Pre-registration: crypto C2M — buy an altcoin after an outsized run-up (2026-11-05)

Committed BEFORE any C2M return is computed and before any held-out candle is downloaded.

## Where this comes from (stated plainly)
C2 (prereg_crypto_pumpfade_2026-11-05) shorted coin-specific bursts and lost on training data at every hold, stop
and threshold (gross -0.32% per trade over 60 minutes): the bursts kept running. Buying instead is a hypothesis
formed FROM that training result, so training data cannot test it fairly. The development gate below is reported
for completeness; the decisive test is the held-out period 2026-05-01 .. 2026-10-31, never downloaded so far.
New vault study `crypto-momentum-2026-11` (same dates as `crypto-leadlag-2026-11`) makes that test one-shot.

## Event and trade (primary, the only test that decides)
- Events: exactly C2's (`sgt.crypto.pumpfade.events`, same 19 alts, BTC-USD market, r30 >= max(3%, own 99.9th
  percentile over the prior 30 days), excess over BTC >= 2%, one event per coin per 6 hours).
- Trade: BUY at the open of bar t+1. Stop: the first bar in t+1 .. t+60 whose low <= 0.90 x entry sells at
  min(0.90 x entry, that bar's open). Otherwise sell at the close of the last candle in t+1 .. t+60.
- Cost (primary): 0.10% round trip. [VERIFY] before any money: this edge is far smaller than retail spot costs
  (Coinbase retail spot ~1.2% round trip; Robinhood's spread is not published), so only a venue at about 0.10% round
  trip or less could carry it.

## Gates (same criteria as C1/C2: >= 200 events dev / >= 50 held-out; mean net > 0; day-cluster bootstrap
one-sided 95% lower bound > 0, 10,000 resamples, seed 20261105; both halves positive (dev split 2025-03-01,
held-out split 2026-08-01); no single month > 50% of the total)
- Development (training 2024-01..2026-04): reported, not independent evidence (see above).
- Held-out (decisive, run once after the vault unlock): PASS -> 24/7 paper texts on the runner, forward n >= 300
  and a verified low-cost venue before any money. FAIL -> written up, nothing deployed.

## Secondary (reported only)
Hold 15 / 30 / 120 min; costs 0.02% / 0.30% / 1.20%; hit rate; per coin.
Trial ledger run ids `crypto-momentum-2026-11-05-dev`, `crypto-momentum-2026-11-05-holdout`.

## Result (run once 2026-11-05; vault unlocked at 4bc5e6e) — FAIL

| measure | development 2024-01..2026-04 (not independent) | held-out 2026-05..10 (decisive) |
|---|---|---|
| trades | 1,416 | 241 |
| primary mean net (60 min, 10% stop, 0.10% cost) | +0.155% | +0.013% |
| gross | +0.255% | +0.113% |
| hit rate | 50% | 43% |
| bootstrap lower 95% | -0.138% | -0.294% |
| halves | +0.19% / +0.10% | +0.36% / -0.16% |
| largest month share of total | 49% | 464% (total near zero) |
| hold 15 / 30 / 120 min, net | -0.01% / -0.04% / +0.26% | -0.00% / +0.01% / +0.03% |
| cost 0.02% / 0.30% / 1.20%, net | +0.24% / -0.04% / -0.94% | +0.09% / -0.19% / -1.09% |
| coins positive | — | 10 of 19 |

Reading: the held-out period shows no edge after a 0.10% cost: the mean is indistinguishable from zero, the second
half is negative and any retail cost turns it clearly negative. Buying coin-specific bursts does not pay, just as
shorting them (C2) does not. Verdict FAIL; nothing deployed; the vault study is spent. No Robinhood or other crypto
connector is built for this line.
