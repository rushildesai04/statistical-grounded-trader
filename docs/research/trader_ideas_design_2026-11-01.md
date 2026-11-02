# Trader ideas (AAOI, beta, timing, institutions): evidence and system design (2026-11-01)

## 1. What the trader said, and what the data says

Every test below was frozen in a pre-registration before its held-out data was examined, and
scored once (files in `docs/findings/`, runs in the trial ledger).

| Claim | How it was tested | Result |
|---|---|---|
| AAOI / AAOX / AAOZ are the most likely to make money | Pair check on real prices + 10 years of synthetic 2x funds (`aaoi_leveraged_pair_2026-11-01.md`) | Long both: -55% in 76 days. Short both: wins 2 months in 3, loses ~2x the position in rally months; negative on average; not shortable at Alpaca. |
| AAOI's beta is ~4x | Daily beta vs SPY | **True:** 1.33 (2016-23), 3.86 (2024-26), 5.19 (last 60 days). But R-squared is 0.21: 79% of AAOI's moves are stock-specific. |
| Morning money / power hour | Time-of-day profile (training); published 5-minute opening-range breakout on AAOI + 7 peers, held-out 2024-26 (`prereg_aaoi_intraday`) | Volume is U-shaped (open 15%, close 16% of the day): **true**. Direction is not: 49% of opening half hours are up. ORB: pooled -0.055%/trade (1,423 trades); AAOI +0.12%/trade but driven by 5 trades (84% of trades lose). **FAIL.** |
| Institutions keep buying (VWAP) | VWAP trend rule, 1-60 minute re-checks, 8 names, training | Gross ~0 on 8/8 names; net negative at every interval. Not carried to held-out. |
| Institutions keep buying, "wash" = VWAP; rules learned by a model (2026-11-02) | LightGBM on VWAP, FINRA short volume, 13F breadth across ~1,650 stocks/day; entry/exit/stop learned on 2016-22; held-out 2023-26 once (`prereg_inst_flow_2026-11-02`) | Flow data adds information over beta/momentum (**T2 PASS**), but the trade fails (**T1 FAIL**): +1.9 bp/day, decayed to ~0 in 2025-26, profit from the short side only, VWAP adds nothing. |
| Leveraged-ETF flows push the close (mechanism behind AAOX/AAOZ + power hour) | Last-half-hour continuation on 16 stocks with 2x funds, before vs after launch (`prereg_letf_close`) | Post-launch -0.105%/trade (2,628 trades); no change vs pre-launch; no effect where fund volume is heaviest. **FAIL.** |

What has passed so far: the MetaTradings alert fade (narrowly; only on hard-to-borrow names),
turn-of-month SPY (small), and passive 60/40. The common thread is **forced flows and constraints**
(month-end cash flows, the inability to short hot small caps), not chart patterns or time-of-day
rules. That points to where the trader's "institutions" intuition can actually be measured.

## 2. Why the trader can still be making money

Rules that fail as written can still hide a discretionary edge: he may pick a small subset of
setups by feel, size up on the best ones, or exit early in ways he does not describe. None of that
is visible in a stated rule. The direct test is his **actual trade log** (date, time, symbol, side,
entry, exit, size) for his last 30-50 trades. It takes one evening to score against the same
1-minute data and answers "does this trader have an edge" without guessing his rules. This is the
single most valuable thing to ask him for.

## 3. Algorithm improvements

### 3.1 Beta and volatility belong in the risk layer, not the signal
Beta is confirmed and large, but it measures exposure, not expected profit. Use it in three places:

| Use | Rule | Effect |
|---|---|---|
| Position size | dollars = (equity x risk per trade) / (stop distance or ATR%); stop distance from the stock's own volatility | AAOI (8% daily vol) gets about a quarter of the dollars of a 2%-vol name for the same risk |
| Portfolio cap | sum of (beta_i x weight_i) <= limit, e.g. 1.0 | Ten AAOI-like longs cannot quietly become 4x the market |
| Hedge (optional) | short beta x weight of QQQ against each long | Leaves only the stock-specific part (79% for AAOI); worth it only for a signal that predicts the stock relative to the market |

Effort: about one day, in the shared risk module, used by every forward test and any future live
strategy.

### 3.2 Institutional activity: measure it properly, across many stocks
"Every ticker is different" is true, but one ticker has too little data to learn from: AAOI
produced 212 opening-range trades in three years, and its result hinged on 5 of them. The standard
fix is to test the institutional signal across hundreds of stocks at once, letting each ticker
differ through a shrunk (pooled) per-ticker effect rather than one model per ticker.

| Source | What it shows | Delay | Status |
|---|---|---|---|
| SEC Form 13F data sets | Holdings of every institution > $100M, per stock, quarterly | 45 days after quarter end | Reachable (checked) |
| FINRA daily short sale volume | Short volume share per stock per day | Next morning | Reachable (checked; AAOI and AAOX present) |
| FINRA OTC/ATS transparency | Dark-pool (ATS) volume per stock per week | 2-4 weeks | Public; not yet checked |
| SIP trade prints (Alpaca) | Off-exchange share, large prints, buy/sell pressure | Real time | Available; heavy data |

Literature: increases in the NUMBER of institutional holders ("breadth") predicted higher returns
in older samples (Chen, Hong & Stein 2002); mutual-fund buying predicted modest outperformance
(Chen, Jegadeesh & Wermers 2000). Effects shrink after publication and the 45-day delay costs
more. Proposed test (one family, pre-registered, liquid stocks > $5): (a) quarterly breadth change
-> next-quarter return, (b) daily short-volume share and ATS share -> next 5-20 day returns, with
held-out 2023-2026. Effort: 2-3 days (bulk downloads, CUSIP-to-ticker mapping, point-in-time
handling).

If "wash method" means something specific to him (VWAP algos, Level 2 tape, dark-pool prints,
"washouts" before a run-up), we need his exact definition. Each is a different dataset.

### 3.3 Keep per-ticker "brains", but pool them
The megacap forward test already uses per-ticker models. Extending to AAOI-type names should use
one pooled model with ticker-specific adjustments (e.g. hierarchical or ticker as a feature with
shrinkage), trained on many tickers. The 12-unseen-ticker test (Sharpe 0.44) showed that
per-ticker results on a few hand-picked names mostly reflect which names were picked.

## 4. Agent structure

The rule: language models turn ideas into testable specifications and explain results; they never
see held-out data, never score, and never place orders. Scoring, risk and execution stay
deterministic and auditable.

| Role | Who | Inputs | Outputs | Guardrail |
|---|---|---|---|---|
| Intake | LLM agent | Trader brain dumps, Telegram, articles | Hypothesis card: claim, mechanism, draft rule, data needed | No market data access |
| Rule freeze | Claude + you | Card | Pre-registration doc + scorer code, committed | Commit hash recorded before scoring |
| Holdout vault | Code (new) | Study id, date ranges | Refuses reads of held-out dates except from a scorer tied to a committed prereg | Every access written to the trial ledger |
| Scorer | Deterministic script | Frozen rule, data | Verdict, ledger entry | Runs once; family-wise alpha |
| Forward runner | Fly cron (exists) | Passed rules | Paper ledgers, monthly ledger entries | No orders unless promoted |
| Promotion gate | You | Forward results | Go / no-go for capital | Written criteria in the prereg |
| Risk + execution | Code (exists for 60/40; extend) | Approved signals | Orders | Per-trade risk, beta cap, daily loss stop, kill switch |
| Reporting | Code (exists) | Ledgers, logs | SMS: failures now; daily digest next | No secrets in messages |

What is new is the **holdout vault** and the **intake card format**. Today the held-out discipline
is enforced by habit; the more trader ideas we test, the more it matters that the code enforces it.
The vault is a thin wrapper around `ParquetBarStore.read_frame` plus a registry file of study
ranges. Effort: about one day with tests. The intake card is a template plus a prompt. Effort:
half a day.

## 5. Roadmap

| Step | Value | Effort | Needs |
|---|---|---|---|
| 1. Trader's last 30-50 trades scored | Highest: tests his real edge directly | 1 evening | His trade log |
| 2. Beta/vol risk layer | Safer sizing for everything | ~1 day | — |
| 3. Holdout vault + intake cards | Keeps future tests honest at scale | ~1.5 days | — |
| 4. Institutional-flow test (13F breadth, short volume, ATS) | The trader's "most important" factor, tested properly | 2-3 days | A contact string for SEC's user-agent rule |
| 5. Daily SMS digest | You see forward results without opening a laptop | ~0.5 day | — |

Running in parallel already: megacap forward test, ETF candidates, turn-of-month, 60/40 paper
(Fly), and the alert-fade forward record (laptop job, once installed).

## 6. Questions for the trader
1. Your last 30-50 trades: date, time, symbol, side, entry, exit, size.
2. When you say institutions are buying, what do you look at on screen: VWAP, Level 2, dark-pool
   prints, 13F filings, something else? What exactly does "wash" refer to?
3. For AAOX/AAOZ: the exact entry trigger, exit, and stop you use.
4. Which time windows do you trade (e.g. 09:30-10:00 only), and which setups do you skip?
