# Pre-registration — AAOI daily "swing" capture (2026-11-12)

**Status at commit: FROZEN before any strategy code is written or run.** This file is committed first;
the study script (`scripts/aaoi_swing_study.py`) and tests (`tests/research/test_aaoi_swing.py`) are
committed second; the single run and its Result section are committed third. Numbers below the
"Result" marker do not exist until the run.

## Owner claim (2026-11-12, verbatim)
"Add AAOI to your stable alpha. It's good for at least $5 per share swing EVERYDAY, go do research on
this and complete this in full too in tandem."

## 0. Fact check of the claim (descriptive, computed before this prereg)
RTH 09:30-15:59 ET daily bars built from local 1-minute bars (`data/alpaca`). No split/gap days were
found (open/prev-close ratio stayed inside [0.4, 2.5] for every session), so raw minute prices are
used as-is.

| year | sessions | typical close | range $ median | range $ p25 | days range ≥ $5 | range % median | \|open→close\| $ median | days \|o→c\| ≥ $5 | open→close % median (signed) |
|---|---|---|---|---|---|---|---|---|---|
| 2024 | 252 | $13.94 | 1.10 | 0.68 | 2.4% | 7.69% | 0.48 | 1.2% | −0.73% |
| 2025 | 250 | $25.27 | 2.07 | 1.48 | 4.0% | 8.60% | 0.98 | 1.6% | −0.72% |
| 2026-YTD | 209 | $107.65 | 10.65 | 6.14 | 84.2% | 9.92% | 4.15 | 42.1% | −0.26% |

**Reading of the claim.** The "$5 per share every day" is true only in 2026, and only because the
share price rose from ~$14 to ~$108. In dollar terms the range scales with price; in *percent* terms
it is a steady ~8-10% per day in all three years. The claim therefore describes AAOI's high volatility
at today's price, not a new fact. **Critical caveat (stated before any test): a range is not a profit.**
Capturing a $5 intraday range requires knowing the direction and the timing of the high and the low in
advance; neither is known at entry. The naive "buy the open, sell the close" has a *negative* median
every single year (−0.73% / −0.72% / −0.26%), so simply being in AAOI intraday loses on the typical
day. This study asks the only answerable version of the claim: **is there a pre-specifiable, closed-bar
signal that turns some of AAOI's daily movement into positive net expectancy out of sample?**

## 1. Scope and what is NOT re-tested
Already decided FAIL / not-pursued (not repeated here):
- 5-minute opening-range breakout on AAOI and peers — FAIL (`prereg_aaoi_intraday_2026-11-01`).
- Daily per-stock LightGBM model on AAOI — FAIL, −6.7%/yr vs hold +54%/yr (`prereg_aaoi_daily_2026-11-06`).
- AAOI/AAOX leveraged pair — not pursued (`aaoi_leveraged_pair_2026-11-01`).
- Single chart indicators (EMA cross, RSI, MACD, VWAP) — ~0 edge before costs (PLAYBOOK finding 10).

The live 15-name paper forward book (`src/sgt/megacap/live.py`, `prereg_megacap_ext_forward_2026-11-09`)
already contains AAOI and is **not touched** by this study.

## 2. Data, split, costs
- Source: `sgt.intraday.store.ParquetBarStore("data/alpaca")`, `read_frame("AAOI", ...)`, column
  `segment == "rth"`, `minute_of_day` in [570, 959] (09:30-15:59 ET). Timestamps UTC.
- Daily RTH bar per session: `open` = first RTH bar open, `high` = max high, `low` = min low,
  `close` = last RTH bar close. A session is used only if it has the full RTH span
  (`minute_of_day` reaches 959) so the close is the real 15:59 close.
- Split guard: any session whose `open / prev_close` is outside [0.4, 2.5] is dropped, and so is any
  trade whose entry or exit session is such a day (none exist in the current data, but the guard is kept).
- **Primary cost: 0.10% round trip** (subtract 0.0010 from the gross fractional return of each trade).
  **Secondary cost: 0.04% round trip** (0.0004), reported alongside. Same convention as
  `prereg_aaoi_daily_2026-11-06`.

## 3. Split of history (fixed, before the run)
- **Training**: sessions with date ≤ 2024-12-31.
- **Held-out**: sessions 2025-01-01 .. latest local date (2026-10-30). Held-out is scored for a
  candidate **only if that candidate passes on training**; otherwise it is not opened for that candidate.

## 4. Candidate rules (k = 3, chosen a priori, each distinct from §1)
All rules are long-only (AAOI is hard to borrow and carries a high beta; the house short rule F7
blocks shorts at beta ≥ 3). Every decision uses only closed sessions ≤ T; entry is always at the
**next** session's open (T+1); there is no "buy the low" hindsight. One trade per signal.

Let `cc_T = close_T / close_{T-1} − 1` (close-to-close) and
`pos_T = (close_T − low_T) / (high_T − low_T)` (where the close sits in the day's range).

- **C1 — Trend-day momentum continuation (1-session hold).** *Reason:* AAOI is a high-beta AI-optics
  momentum name; a strong up day that closes near its high reflects demand that, a priori, is more
  likely to follow through the next session than to reverse. *Signal on closed T:* `cc_T ≥ +0.04`
  **and** `pos_T ≥ 2/3`. *Trade:* long at `open_{T+1}`, exit at `close_{T+1}`.
  Net = `close_{T+1} / open_{T+1} − 1 − cost`.

- **C2 — Oversold one-day bounce (1-session hold).** *Reason:* sharp single-day drops in high-vol
  names over-shoot; a mean-reversion bounce the next session is the opposite-sign a-priori bet to C1.
  *Signal on closed T:* `cc_T ≤ −0.05`. *Trade:* long at `open_{T+1}`, exit at `close_{T+1}`.
  Net = `close_{T+1} / open_{T+1} − 1 − cost`.

- **C3 — 20-session Donchian breakout, 3-session swing hold.** *Reason:* a close above the highest
  close of the prior 20 sessions marks a new demand regime; capture multi-day follow-through rather
  than a single session. *Signal on closed T:* `close_T > max(close_{T−20 .. T−1})`. *Trade:* long at
  `open_{T+1}`, exit at `close_{T+3}` (fixed 3-session hold). **Non-overlapping:** a new breakout is
  ignored while a C3 position is still open. Net = `close_{T+3} / open_{T+1} − 1 − cost`.

Signal counts observed on training during the §0 fact check (frequency only, no P&L): C1 = 299,
C2 = 229, C3 = 270 raw breakout days (non-overlapping count determined at run time). All clear the
100-trade minimum except possibly C3 after de-overlap; see §5.

## 5. Statistic and PASS criteria (frozen)
- **Bootstrap:** day-clustered, clustered by entry date, 10,000 resamples, seed **20261112**
  (same estimator as `boot_means` in `scripts/telegram_alert_test.py`).
- **Multiplicity:** Bonferroni, family one-sided α = 0.05 across k = 3 → per-candidate
  α = 0.05/3 = 0.016667. The **primary lower bound** is the 0.016667 quantile of the bootstrap means.
  The plain one-sided 95% lower bound (0.05 quantile) is also reported.
- A candidate **PASSES** iff, at the **primary 0.10% cost**:
  1. `n_train ≥ 100` trades, **and**
  2. training mean net > 0, **and**
  3. training Bonferroni lower bound (0.016667 quantile) > 0, **and**
  4. held-out mean net > 0 (held-out opened only after 1-3 hold).
  Any candidate with `n_train < 100` fails the minimum-trade gate and is reported, not passed.
- **Hold benchmark (reported for every candidate):** the mean net per-trade return of going long
  `open_{t}` → `close_{t+h}` on **every** eligible session in the same period (same horizon `h` as the
  candidate: h = 1 for C1/C2, h = 3 for C3), net of the same round-trip cost. This isolates whether the
  signal beats indiscriminate daily holding. AAOI buy-and-hold total return over each period is also
  reported for context.

## 6. Known limitations (stated in advance)
- C3's overlapping-signal correlation is reduced by the non-overlapping rule; residual serial
  dependence is handled only approximately by entry-date clustering.
- 2026 dominates the dollar range; because the test is in *percent* returns this does not advantage the
  rules mechanically, but the held-out window (2025-2026) is a single strong regime for the underlying,
  so a PASS would still need live confirmation under the house proof standard (n ≥ 300 live, CI > 0).
- Intraday timing/stops are deliberately excluded: entries and exits are at session open/close only, so
  no result here depends on knowing the high or low in advance.

---

## Result
Run once on 2026-11-12 (`scripts/aaoi_swing_study.py`, seed 20261112, local data through the
2026-10-30 session). Output: `reports/aaoi_swing/result.json` + `trades_*.csv`.

**VERDICT: all three candidates FAIL.** Each has a positive training mean net, but its day-clustered
bootstrap lower bound is well below zero — even the plain one-sided 95% bound, before Bonferroni — so
none clears the training gate and the held-out window was not opened for any of them.

### Training (entry_date ≤ 2024-12-31), primary cost 0.10% round trip
| candidate | hold | n | mean net | mean net @0.04% | gross | win | Bonferroni LB (α=0.0167) | plain 95% LB | gate: n≥100 / mean>0 / LB>0 | all-day hold bench (same horizon) |
|---|---|---|---|---|---|---|---|---|---|---|
| C1 momentum continuation | 1 d | 298 | +0.220% | +0.280% | +0.320% | 47% | **−0.438%** | −0.301% | ✓ / ✓ / ✗ → FAIL | −0.031% (n 2245) |
| C2 oversold bounce | 1 d | 229 | +0.440% | +0.500% | +0.540% | 52% | **−0.346%** | −0.176% | ✓ / ✓ / ✗ → FAIL | −0.031% (n 2245) |
| C3 20-d Donchian breakout | 3 d | 123 | +0.462% | +0.522% | +0.562% | 52% | **−1.531%** | −1.079% | ✓ / ✓ / ✗ → FAIL | +0.322% (n 2226) |

Held-out (2025-01-02 .. 2026-10-30) was **not opened** for any candidate (none passed training).

### Buy-and-hold comparison (the honest benchmark)
| period | AAOI buy-and-hold total return |
|---|---|
| training 2016-01-04 → 2024-12-31 | **+117.2%** |
| held-out 2025-01-02 → 2026-10-30 | **+92.0%** |

### Plain-language reading
The owner's "$5 swing every day" is a real description of AAOI *today* — at ~$108 a share the median
day really does travel ~$10.65 and 84% of 2026 days span ≥ $5 — but it is not money. The same stock in
2024 (~$14) swung ~$1.10 on a median day; the $5 figure grew because the price grew, while the *percent*
swing stayed ~8-10% every year. A range is only profit if you know the direction and the timing of the
high and low in advance, and the typical day actually drifts slightly **down** open-to-close.

We pre-registered the three most defensible ways to turn that daily movement into a directional long
bet — ride a strong up-close, buy an oversold drop, and ride a 20-day breakout — and tested them exactly
once out of sample under real costs. All three show a small positive *average* in training, but the
bootstrap says that average is indistinguishable from luck: the lower edge of the confidence interval is
negative in every case, so we cannot claim any of them makes money. The momentum and bounce rules barely
beat simply being in the stock on random days (which itself loses −0.03%), and the breakout rule does not
beat it meaningfully. Crucially, **just holding AAOI returned +117% over the training span and +92% over
the held-out span** — vastly more than any per-trade swing rule, and with no trading. Consistent with
PLAYBOOK finding 10 (chart signals carry ~0 edge) and the two prior AAOI studies, there is no evidence
here for a tradeable daily AAOI swing edge. AAOI already sits in the live 15-name paper forward book;
nothing in this study changes that, and no new money or data is warranted.
