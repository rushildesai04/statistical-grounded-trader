# Statistical-Grounded Trader — Intraday Signal Engine Implementation Plan

**Status:** Draft v0.1 (2026-10-12) — planning only, no implementation started
**Owners:** Rushil (modeling/architecture), Neel (product)
**Extends:** `docs/SPEC.md` (base platform: ingestion, point-in-time features, walk-forward backtest). Nothing in the base is removed; this plan adds an intraday track on top of it.
**Source material:** trader walkthrough (transcript parts 1–2, video part 1, 2026-10-12): 1-minute low-float momentum scalping using EMA 9/21(24) crossovers, volume confirmation, PVT, PSAR, MACD histogram exits, RSI anti-chase filter, power-hour timing, and a Telegram alert channel ("NEWS, VOLUME, BREAKOUTS") as the ticker source.

---

## 1. Objective and what "working" means

We are building a system that (a) discovers tickers worth watching from market and crowd signals, (b) decides when to enter and exit using combinations of lightweight, explainable signals, (c) learns from every simulated and real trade, and (d) can execute autonomously across multiple brokerage accounts behind hard guardrails, switched on and off by a human.

"Make money, don't lose thousands" is translated into measurable acceptance criteria. A strategy is only allowed to trade real capital when all of the following hold, and it is automatically demoted when they stop holding:

| Criterion | Threshold (initial, tunable) | Measured on |
|---|---|---|
| Net expectancy per trade after modeled costs | > 0 with 95% bootstrap CI lower bound > 0 | Walk-forward out-of-sample + lockbox |
| Deflated Sharpe Ratio (corrects for number of configs tried) | > 0.95 probability of true Sharpe > 0 | Full search history from the strategy registry |
| Profit factor | ≥ 1.3 | Out-of-sample |
| Max drawdown | ≤ 50% of the strategy's allotted capital-at-risk budget | Out-of-sample + shadow |
| Shadow-vs-backtest agreement | Signal match ≥ 98%; modeled fill slippage within 1.5× of backtest assumption | Shadow mode, ≥ 4 weeks |
| Live-vs-shadow agreement | Realized slippage ≤ 1.5× modeled; PnL within expected band (see §11.4) | Live micro-size, ≥ 4 weeks |

Non-goals for this plan: deep learning (RNN/LSTM/transformer), reinforcement learning, high-frequency/sub-second strategies, shorting, options, and managing money for anyone other than the account owner (that changes data licensing and regulatory status; see §15).

**The capital-protection doctrine, stated up front.** Losing money in small, bounded, pre-approved amounts while learning is acceptable; losing it in large, unbounded, or unnoticed amounts is not. Every mechanism in §11 (loss budgets, capital ladder, decay detection, kill switches) exists so that a strategy that stops working is detected and switched off automatically, long before losses compound — the specific failure mode of "it worked for months, then quietly lost thousands."

---

## 2. Reality constraints that shape the design

| Constraint | Consequence for the plan |
|---|---|
| **Free real-time data is IEX-only** (~2.5% of US volume on Alpaca's free tier). | Free real-time feeds cannot be used to compute volume signals. During the free phase, forward testing runs on **15-minute-delayed consolidated (SIP) data** in "delayed shadow" mode (§4.3). This is valid because shadow mode places no orders, so latency doesn't matter — only data fidelity does. |
| **Free historical intraday data is limited.** Alpaca's free plan allows historical SIP bars older than 15 minutes, but history depth and delisted-symbol coverage are inconsistently documented. yfinance 1-min data only covers recent days. | Phase 1 begins with a data audit that measures exactly what the free tier returns (depth, delisted names, extended hours, gaps) before any research depends on it. Survivorship bias is quantified, not assumed away. Paid history (Databento or Massive) is the escalation path. |
| **Robinhood has no official equities trading API.** Only a crypto API exists; the ToS prohibits unofficial clients; aggregators list Robinhood as read-only. | Robinhood is supported as **read-only** (positions/balances via aggregator) plus **alert-only** mode (the system tells the human what to do). We will not use unofficial/reverse-engineered clients: they risk account lockout, break without notice, and violate the ToS. Revisit if Robinhood ships an official equities API. |
| **Schwab's Trader API refresh token expires roughly every 7 days.** | Schwab connector includes a scheduled weekly human re-authorization step with advance alerts; if the token lapses the connector fails closed (no new orders, existing broker-side stops remain). |
| **Pattern Day Trader rule eliminated** (SEC approval 2026-04-14, effective 2026-06-04; brokers implementing through 2027-10-20). | No PDT counter needed long-term, but each broker's implementation status is tracked per account. Cash/IRA accounts still face settlement rules, so the risk layer tracks **settled cash** per account. |
| **Low-float runners halt, gap, and have wide spreads.** | Cost model is spread- and liquidity-dependent, not a fixed bps figure. Halts are first-class events. Marketable-limit orders only; never market orders on thin names. |
| **Every indicator is lagging** (trader's own words). | We do not attempt price prediction. We select which rule-generated candidates to take and how to size them (meta-labeling, §9). |

---

## 3. Architecture

### 3.1 One engine, four modes

The single most important design rule: **the same code computes signals and decisions in backtest, shadow, paper, and live.** Only the data source and the broker adapter change. This eliminates backtest/live divergence as a class of bug and makes every live decision replayable.

| Mode | Market data source | Broker adapter | Real orders | Purpose |
|---|---|---|---|---|
| `backtest` | Historical store (Parquet) | `SimBroker` (conservative fill model) | No | Research, parameter search |
| `shadow` | Live or 15-min-delayed SIP stream | `SimBroker` | No | Forward test on unseen data |
| `paper` | Live stream | Broker paper endpoint (e.g. Alpaca paper) | Paper only | Exercise the real order lifecycle |
| `live` | Live stream | Real broker adapter(s) | Yes | Production, behind guardrails |

### 3.2 Data flow

```
┌───────────────────────────── Ingestion ─────────────────────────────┐
│ Market: bars/trades/quotes (Alpaca free → Databento/Massive paid)   │
│ Reference: shares outstanding/float (EDGAR, vendor), short interest │
│ Events: halts (Nasdaq Trader), filings (EDGAR), news (existing)     │
│ Crowd: Telegram channels, StockTwits, Reddit (existing collectors)  │
└──────────────┬───────────────────────────────────────┬──────────────┘
               ▼                                       ▼
   Parquet market store (partitioned            Postgres event store
   by date/symbol, immutable)                   (append-only, point-in-time)
               │                                       │
               └──────────────┬────────────────────────┘
                              ▼
                 Bar builder + session calendar + halt state
                              ▼
                 Indicator engine (incremental, O(1)/bar)
                              ▼
   Scanner (which tickers) ──▶ Watchlist ──▶ Strategy runtime (grammar-defined)
                                                  ▼
                                   Meta-filter (LR / small GBM): take/skip + size
                                                  ▼
                                   Portfolio & risk manager (fail-closed)
                                                  ▼
                                   Account router ──▶ Broker adapters (N accounts)
                                                  ▼
   Event log (every bar, signal, decision, order, fill, reject) ──▶ Trade journal
                                                  ▼
   Feedback loop: outcome labeling → drift/decay monitors → retrain → challenger → promotion gate
```

### 3.3 Technology choices

| Concern | Choice | Rationale |
|---|---|---|
| Language/runtime | Python 3.11+, `asyncio` for live I/O | Matches existing repo; minute-bar strategy has no need for a lower-latency language |
| Columnar market storage | Parquet (pyarrow), queried with DuckDB | Fast, cheap, immutable files; DuckDB gives SQL over Parquet with no server |
| Events, registry, journal | Postgres via existing SQLAlchemy models (SQLite in local dev) | Already in the base; transactional, auditable |
| Numerics | numpy; numba only if profiling shows a need | Keep dependencies light |
| Lightweight ML | scikit-learn (logistic regression, existing), LightGBM (depth ≤ 4) | Microsecond inference, small artifacts, feature attributions |
| Config/validation | pydantic v2 (existing) | Strategy configs validated at the boundary |
| Brokers | `alpaca-py`, `ib_async`, Schwab Trader API client, Webull OpenAPI SDK, SnapTrade SDK | Official or de-facto standard clients; versions pinned at implementation time |
| Telegram | Telethon (user-session client, read-only) | Reads channels the account is subscribed to |
| Control plane / dashboard | Existing FastAPI app + minimal UI | Master switch, status, journal browsing |
| Monitoring | Prometheus metrics + Grafana, or Datadog (org already has it) | Alerting on feed health, PnL drift, guardrail trips |

All versions are pinned in `pyproject.toml` when each dependency is introduced.

---

## 4. Data plan

### 4.1 Free-first strategy and its limits

| Need | Free source | Known limitation | Paid upgrade |
|---|---|---|---|
| Historical 1-min bars, consolidated | Alpaca free tier, `feed=sip`, data older than 15 min | History depth and delisted coverage unverified; IEX-only for the latest 15 min | Databento (identical historical/live schema) or Massive Advanced |
| Historical trades/quotes (for spread/slippage modeling) | Alpaca free tier (verify availability/depth) | Possibly shallow | Databento |
| Recent intraday sanity checks | yfinance | ~7–30 days of 1-min only; unofficial | — |
| Live data for shadow mode | Alpaca free, 15-min-delayed SIP | 15-min delay (acceptable for shadow, unacceptable for live) | Alpaca Algo Trader Plus (~$99/mo SIP) or Databento live |
| Shares outstanding | SEC EDGAR (XBRL company facts) | Float must be derived (outstanding minus insider/affiliate holdings), lags filings | Float vendor |
| Short interest | FINRA bi-monthly files | Twice a month, delayed | Ortex-type vendors |
| Halts | Nasdaq Trader halt feed | Must poll; verify format/coverage | Included in some paid feeds |
| Filings (dilution) | SEC EDGAR full-text and submissions API | Rate-limited (fair-access policy) | — |
| Crowd/catalyst | Telegram (Telethon), StockTwits, Reddit (existing), news (existing) | ToS constraints per source | X/Twitter API (deferred) |

### 4.2 Data audit (first task of Phase 1)

Before research depends on free data, run an audit script that reports, for a sample of 200 low-float tickers including at least 50 delisted since 2023: history depth available, extended-hours coverage, missing-minute rate, halt-gap representation, and agreement with a second source on overlapping days. Output is a short written finding in `docs/` with a go/no-go on free data for each research question. If delisted coverage is poor, every backtest report carries an explicit survivorship-bias warning until paid data is in place.

### 4.3 Delayed shadow mode

The live engine subscribes to 15-minute-delayed SIP bars and processes them exactly as it would live data, with `SimBroker` filling against those bars. The decisions, fills, and PnL are identical to what real-time shadow would produce — only the wall-clock time at which a human could observe them differs. This lets us run months of forward testing at zero data cost. Live trading requires the paid real-time feed; this is enforced in config validation (live mode refuses to start on a delayed or IEX-only feed).

### 4.4 Storage layout and data contracts

```
data/market/bars_1m/date=YYYY-MM-DD/symbol=XYZ.parquet   (immutable once the session closes)
data/market/trades/...  data/market/quotes/...            (when available)
Postgres: halts, filings, crowd_events, telegram_alerts, float_snapshots,
          short_interest, strategy_registry, trials, event_log, trade_journal, accounts
```

Every row carries `event_time` (when it happened) and `ingested_at` (when we learned it). Features at decision time `t` may only use rows with `event_time < t` **and** `ingested_at ≤ t` in shadow/live replay. This extends the base spec's leakage discipline to intraday and is enforced by the existing leakage-test pattern (`tests/test_leakage.py`).

### 4.5 Session calendar

A single module defines pre-market (04:00–09:30 ET), regular session (09:30–16:00), opening hour (09:30–10:30), midday lull (~11:00–14:00), power hour (15:00–16:00), after-hours (16:00–20:00), exchange holidays and early closes. All times stored in UTC, displayed in ET. Strategies reference named windows, never raw clock times.

---

## 5. Repository layout (additions)

New packages live beside the existing ones under `src/sgt/`. Each stays one-concern and under ~500 lines per module.

```
src/sgt/
  intraday/        bar builder, session calendar, halt state, data audit
  indicators/      EMA, SMA, MACD, RSI, PVT, PSAR, VWAP, ATR, ADX, EWO, SuperTrend, rel-vol, float rotation
  crowd/           telegram collector + alert parser, attention features, platform-stage features
  catalysts/       EDGAR filings (dilution flags), halts collector, catalyst classification
  scanner/         universe filters, ranking, watchlist publication
  strategy/        grammar schema (pydantic), runtime state machine, built-in "trader_v0" config
  engine/          event loop, mode wiring, replay, SimBroker + fill/cost model
  search/          grid/combination enumeration, walk-forward runner, deflated Sharpe, lockbox
  learning/        labeling (triple barrier), meta-filter training, drift/decay monitors, champion/challenger
  risk/            guardrails, capital ladder, loss budgets, kill switches, settled-cash tracker
  brokers/         adapter interface + alpaca, ibkr, schwab, webull, snaptrade, alert_only
  accounts/        account registry, allocation, router, reconciliation
  ops/             metrics, alerting, daily reports, health checks
strategies/        version-controlled YAML strategy configs
docs/              SPEC.md, IMPLEMENTATION_PLAN.md (this), runbooks/, findings/
```

---

## 6. Indicator library

Every indicator is implemented once as an incremental class (`update(bar) -> value`, O(1) per bar, explicit warm-up length, `is_ready` flag) plus a vectorized batch function for research. A property test asserts the two produce identical outputs on the same series.

| Indicator | Definition | Parameters searched |
|---|---|---|
| EMA(n) | `e_t = α·C_t + (1−α)·e_{t−1}`, `α = 2/(n+1)` | n ∈ {5, 8, 9, 12} fast; {18, 21, 24, 30} slow |
| SMA(n) | Mean of last n closes | as needed |
| MACD(f, s, sig) | line = EMA_f − EMA_s; signal = EMA_sig(line); hist = line − signal; 4-state color: Up1 (hist>0 rising), Up2 (hist>0 falling), Down1 (hist<0 falling), Down2 (hist<0 rising) — matches the Webull display | (12,26,9) default; f ∈ {8,12}, s ∈ {21,26}, sig ∈ {5,9} |
| RSI(n) | Wilder smoothing of gains/losses | n ∈ {7, 14} |
| PVT + signal | `PVT_t = PVT_{t−1} + V_t·(C_t − C_{t−1})/C_{t−1}`; signal = SMA_m(PVT); state = PVT vs signal; "thinning" = |PVT − signal| shrinking | m ∈ {14, 21}; SMA vs EMA signal |
| PSAR(start, step, max) | Wilder parabolic SAR | (0.02, 0.02, 0.2) default; trader uses (0.01, ·, 0.19) |
| VWAP | Session-anchored, reset at 04:00 and 09:30 variants | anchor choice |
| ATR(n), ADX(n) | Wilder | n ∈ {14} |
| EWO | SMA5 − SMA35 | (5, 35) |
| SuperTrend(n, k) | ATR-band trend | (14, 5) per trader's chart |
| Relative volume | `V_t / mean(V at same minute-of-day over last N sessions)` | N ∈ {10, 20} |
| Float rotation | Cumulative session volume / free float | — |
| Cross strength | `(EMA_fast − EMA_slow) / ATR` | — |

**Golden tests:** indicator outputs are validated against values read off the trader's Webull screen for WFF, YMAT, SPCX, NVDA, AAOI, LITE and WBUY on 2026-10-12 (e.g., PVT 2.59M / Signal 2.59M on WFF at 13:09 local). Acquiring that day's 1-min bars is a Phase 0 action because free intraday history for recent days expires.

---

## 7. Strategy grammar

Strategies are declarative configs, not code. The grammar enforces logical composition by signal family so the system can enumerate combinations without producing nonsense.

### 7.1 Signal families and permitted roles

| Family | Members | May be used as |
|---|---|---|
| Trend (price) | EMA/SMA cross, MACD line vs signal, SuperTrend | Trigger, exit |
| Momentum (price) | RSI, MACD histogram state/slope, ROC, EWO | Setup, filter, exit |
| Flow (volume) | Rel-vol, PVT state/slope, float rotation, VWAP position | Confirmation, exit |
| Volatility/regime | ATR, ADX, Bollinger bandwidth | Regime gate |
| Structure | PSAR (a trailing price level), pre-market high, high of day, prior-day high, round numbers, halt-resume level | Trigger, confirmation, target, exit |
| Context | Session window, float bucket, gap %, catalyst type, crowd stage, dilution flag | Regime gate, meta-filter feature (never a trigger) |

Composition rules enforced by the validator: exactly one trigger; confirmations must come from a different family than the trigger; context signals can gate or rank but cannot trigger; at least one hard stop and one time stop in exits; correlated signals (measured |ρ| > 0.8 on the research set) cannot both count toward a k-of-m confirmation.

### 7.2 Example config (the trader's rules as `trader_v0`)

```yaml
id: trader_v0
version: 1
universe:
  float_max: 40_000_000
  price_min: 0.50
  rel_vol_min: 3.0
  spread_max_pct: 1.5
regime:
  session_windows: [power_hour, after_hours]
  exclude: [halted, within_luld_band_pct: 2.0]
setup:            # optional "armed" state
  any_of:
    - macd_hist_state: Down2          # red bars shrinking
    - pvt_bullish_divergence: {lookback: 15}
trigger:
  ema_cross_up: {fast: 9, slow: 24}
confirm:
  k_of_m: 2
  signals:
    - rel_vol_gt: 3.0
    - pvt_state: above_signal
    - psar_state: below_price
filters:
  - rsi_lt: 70
exits:
  any_of:
    - macd_hist_state: Up2            # dark green -> light green
    - volume_exhaustion: {price_rising_bars: 3, volume_falling_bars: 3}
    - ema_cross_down: {fast: 9, slow: 24}
    - psar_flip: down
    - stop_loss_pct: 3.0
    - time_stop_minutes: 30
    - session_end: flatten
sizing:
  method: fixed_risk
  risk_per_trade_pct_of_allocation: 0.5
```

The grammar's JSON schema is generated from the pydantic models and versioned; every config gets a content hash used as its identity in the registry.

### 7.3 Runtime state machine

`FLAT → ARMED → ENTRY_PENDING → IN_POSITION → EXIT_PENDING → FLAT`, plus `HALTED` and `LOCKED` (risk lockout) as overlay states. Entries are evaluated only on bar close and executed at the next bar's open in simulation (no look-ahead). Every transition is written to the event log with the full input snapshot that caused it.

---

## 8. Simulation engine and cost model

**Fill model (conservative by default):** entry at the next bar's open plus a slippage term; exit likewise. Slippage = `max(half_spread, k × ATR_fraction) × liquidity_multiplier`, where the liquidity multiplier increases with order size relative to the bar's volume. Orders larger than a configurable fraction (default 1%) of the bar's volume are partially filled. When trade/quote data is available, half-spread is measured; otherwise it's estimated from price bucket and float, with deliberately pessimistic defaults.

**Halts:** a position held into a halt is marked to the resumption print, not the pre-halt price. Entries are blocked within a configurable distance of the trading-limit bands that trigger halts.

**Session rules:** forced flatten at session end (configurable per strategy: regular close or after-hours close). Extended hours accept limit orders only, and the simulator rejects anything else.

**Determinism:** given the same data, config, and seed, a backtest produces byte-identical event logs. Replay of a live session's logged bars must reproduce its live decisions exactly; this is a CI test.

**Benchmarks every report includes:** take-every-raw-signal, `trader_v0` rules alone, random entry with the same exit rules (to separate entry skill from exit/regime luck), and buy-at-alert for Telegram-sourced tickers.

---

## 9. Lightweight modeling

### 9.1 Approach: meta-labeling

The rule-based strategy proposes candidates; a lightweight classifier decides take/skip and scales size by confidence. This targets the real edge (selecting which crossovers to take) rather than direction prediction from lagging indicators.

- **Labels:** triple barrier per candidate — +X% profit target, −Y% stop, T-minute time limit. X, Y, T are derived from the strategy's exits so labels match what the strategy actually does.
- **Features (all point-in-time):** cross strength, rel-vol, float rotation, PVT slope and state, MACD histogram state and slope, RSI, PSAR distance, VWAP distance, distance to HOD/pre-market high/round number, gap %, minute-of-day, spread estimate, halt count today, float bucket, catalyst type, dilution flag, crowd attention z-score, crowd platform stage, Telegram alert type and age.
- **Models (in promotion order):** logistic regression (existing baseline) → LightGBM, max depth 4, ≤ 200 trees, monotonic constraints where the direction is known (e.g. dilution flag can only reduce the score).
- **Lightweight budget (hard limits):** CPU inference < 1 ms per candidate, artifact < 5 MB, training on a laptop in < 10 minutes. Anything exceeding these limits is out of scope by definition.
- **Explainability:** each decision logs top feature contributions (coefficients × values for LR; TreeSHAP for GBM). "Why did it buy?" must always be answerable from the journal.
- **Calibration:** isotonic or Platt calibration so the probabilities are meaningful for sizing.

### 9.2 Promotion gate

A model is promoted only if it beats, out of sample and net of costs: (a) take-every-candidate, (b) `trader_v0` rules alone, and (c) the incumbent champion. This reuses the existing `_promotion_gate` concept in `src/sgt/backtest/harness.py`, extended to intraday metrics.

---

## 10. Search and validation (making parameter tuning honest)

| Control | Implementation |
|---|---|
| Bounded, meaningful grids | Parameter ranges in §6; constraint `fast < slow`; coarse grid first, refinement only inside stable regions |
| Combination enumeration | Grammar-valid combinations only; family/correlation rules prune redundant mixes |
| Walk-forward | Rolling train/test windows by calendar week, with purging/embargo of overlapping labels |
| Combinatorially purged CV | For final candidates, to get a distribution of out-of-sample paths rather than a single number |
| Trial accounting | Every config evaluated is recorded in `trials` with its hash; reports compute Deflated Sharpe from the true trial count |
| Plateau selection | Selection prefers the center of a broad high-performing parameter region; a narrow spike is rejected |
| Regime breakdowns | Results by session window, float bucket, trend vs chop day, catalyst vs no-news |
| Lockbox | The most recent N months are untouched by search; opening it is a logged, one-time event per candidate |
| Speed | Indicator arrays cached per parameter value and reused across combinations; vectorized signal evaluation; bar-by-bar only for exits. Target: thousands of configs over a year of 1-min data on ~200 tickers within minutes on a laptop (benchmark in Phase 3) |

Search output for each run: heatmaps per parameter pair, a ranked table with confidence intervals, regime breakdowns, and a written candidate card stored in the registry.

---

## 11. Feedback loop, strategy evolution, and capital protection

The system is not static, but it doesn't learn online either. It improves through a **controlled, versioned, human-approved cycle**: collect → label → analyze → propose → validate → shadow → promote. Nothing changes in live trading without passing the same gates as a new strategy.

### 11.1 Data collected from every trade (simulated and real)

The trade journal stores, per candidate (taken or skipped): the full feature snapshot, config hash, model version and score, the decision and its reason, order intents, broker acknowledgments, fills, slippage vs model, MFE/MAE (max favorable/adverse excursion), exit reason, halt exposure, and outcome label. Skipped candidates are labeled too, so the system learns what it missed as well as what it took.

### 11.2 Scheduled analysis (weekly)

An automated report covers: performance by strategy/config/regime; slippage reality vs model (feeds back into the cost model); feature drift (population stability index per feature); exit-reason attribution (which exits save money, which give it back); Telegram alert-type performance; and candidate improvements surfaced by re-running the search with the newest data. The report proposes changes; a human approves which ones enter validation.

### 11.3 Change management for strategies

Every change — a parameter, a new signal, a new model — produces a new config hash and goes through: backtest → lockbox → shadow (≥ 2 weeks for parameter changes, ≥ 4 for structural ones) → challenger at minimum live size alongside the champion → promotion. Rollback to the previous champion is one command and is automatic on decay detection. Strategy configs live in git; the registry links each hash to its validation evidence.

### 11.4 Decay detection (the "worked for months, then lost thousands" defense)

| Monitor | Trigger | Action |
|---|---|---|
| CUSUM on per-trade PnL vs the strategy's validated expectancy | Statistically significant downward shift | Demote to shadow, alert |
| Rolling 20-trade expectancy | < 0 with CI upper bound < validated expectancy | Halve size, alert |
| Drawdown vs allotted budget | 50% of budget consumed | Halve size; 100% → disable |
| Live vs shadow divergence | Live PnL outside the shadow-predicted band for 10 trades | Halt live, investigate |
| Slippage drift | Realized > 1.5× modeled over 20 trades | Halt live; recalibrate cost model |
| Feature drift (PSI) | > 0.25 on any top-5 feature | Flag; require re-validation before next promotion |
| Win-rate regime break | Binomial test vs validated win rate fails at p < 0.01 | Demote to shadow |

### 11.5 Capital ladder

| Stage | Capital at risk per strategy | Entry requirement | Exit (demotion) |
|---|---|---|---|
| 0 Shadow | $0 | Passed backtest + lockbox | — |
| 1 Micro | $250–$500 allocation, 1-share to minimal-size orders | ≥ 4 weeks shadow meeting §1 criteria | Any §11.4 trigger |
| 2 Small | $1k–$2k | ≥ 4 weeks and ≥ 50 trades at stage 1 within expected band | Any §11.4 trigger |
| 3 Standard | Up to owner-set cap | ≥ 8 weeks and ≥ 150 trades at stage 2 | Any §11.4 trigger |

Promotion between stages requires explicit human approval; demotion is automatic.

### 11.6 Loss budgets (hard, fail-closed)

The owner sets a total **learning loss budget** (for example $500) that caps cumulative live losses before the program pauses for review. Below it: a per-strategy budget, a daily loss limit per account and across all accounts, a per-trade max loss, and a daily profit lock (stop opening new trades after the daily target, the trader's 1% rule). Hitting any budget disables trading at that scope until a human re-enables it. Budgets are enforced in the risk layer before orders reach any broker adapter, and broker-side stop orders back them up where supported.

---

## 12. Crowd psychology and catalyst layer

Crowd and social signals decide **which tickers to watch** and provide **context** for the meta-filter. They never trigger trades directly.

| Crowd behavior | Feature | Source |
|---|---|---|
| Attention | Mention-velocity z-score vs ticker baseline; unique-author count | Telegram, StockTwits, Reddit |
| Diffusion stage | First-seen timestamp per platform; stage = alert-only / StockTwits / WSB / mainstream news | Cross-platform timestamps |
| FOMO / chase | Consecutive new-high alerts, gap %, distance above VWAP, RSI > 70 with rising attention | Price + crowd |
| Anchoring | Distance to round numbers, pre-market high, HOD, prior-day high | Price |
| Halt herding | Halt count/direction; resume above or below the halt price | Halts feed |
| Order-book pressure (paid-data phase) | Bid/ask imbalance, trade rate, large prints at the ask | L1/L2 data |
| Trapped shorts | Short interest % float, days to cover | FINRA / vendor |
| Dilution risk | Recent S-1/S-3/424B/ATM filings, warrant overhang | EDGAR |
| Ramp-and-dump risk | Low float + Telegram-only attention + "no news" + high-risk listing profile | Combined |

**Telegram collector.** Telethon user session reading channels the owner is subscribed to; read-only; never posts. Alerts are parsed against a strict per-channel template grammar (`<TICKER> <alert_type> <price>`, where alert types include `volume increasing`, `gaining volume`, `gaining momentum`, `new high`, `intraday high`, `halted`, `open!`, `after hours high`, plus news/no-news blocks). Unparseable messages are stored raw and flagged, never guessed at. Messages are timestamped by Telegram's server time. Telegram's and the channel's terms are reviewed before enabling.

**Phase 1.5 event study.** Using the channel's history: forward returns at 1/5/15/60 minutes by alert type, news vs no-news, float bucket and time of day; MFE/MAE; halt incidence; and alert latency (how much of the move had already happened when the alert landed). This is the cheapest available evidence of edge and becomes the scanner's first model.

**Untrusted input.** All crowd text is hostile input: size-bounded, strictly parsed, deduplicated, bot-filtered (account age, posting rate, duplicate text). If an LLM is ever used for catalyst classification, it receives bounded text with no tool access and returns a constrained label only.

---

## 13. Brokerage connectivity

### 13.1 Adapter interface

Every broker implements one interface; the strategy and risk layers never see broker-specific code.

```python
class BrokerAdapter(Protocol):
    capabilities: BrokerCapabilities          # order types, extended hours, bracket/OCO support, paper available
    async def connect(self) -> None
    async def account_state(self) -> AccountState      # cash, settled cash, buying power, positions
    async def submit(self, intent: OrderIntent) -> OrderAck   # idempotent via client_order_id
    async def cancel(self, broker_order_id: str) -> None
    async def open_orders(self) -> list[Order]
    def fills(self) -> AsyncIterator[Fill]              # streaming or polled, normalized
    async def health(self) -> AdapterHealth             # auth validity, token expiry, rate-limit headroom
```

`OrderIntent` carries a deterministic `client_order_id` (hash of strategy, account, symbol, signal timestamp), so retries never double-submit. Brokers without native idempotency get a local dedupe ledger checked before submission.

### 13.2 Connector matrix

| Broker | Path | Trading | Notes / risks | Priority |
|---|---|---|---|---|
| **Alpaca** | Official REST + WebSocket (`alpaca-py`) | Yes; paper environment uses the identical API | Fastest build; payment-for-order-flow routing may hurt fills on thin names; verify IRA support | P1 (first adapter) |
| **Interactive Brokers** | TWS / IB Gateway (`ib_async`) | Yes; paper account | Best routing control and order types; gateway process needs daily restart handling | P2 (scale-up venue) |
| **Charles Schwab** | Official Trader API, Individual tier (OAuth) | Yes | ~7-day refresh-token expiry requires weekly human re-auth; 120 non-order requests/min; approval required | P2 |
| **Webull** | Official US OpenAPI (HTTP trading, MQTT data, gRPC order events) | Yes, after application approval | Trader's current platform (Rollover IRA); some features documented as unavailable — verify; key regeneration limited to 3/day | P3 (verify first) |
| **SnapTrade (aggregator)** | Unified API across brokers | Read+trade for Webull, E*TRADE, tastytrade, Moomoo, Public, others; Schwab requires your own commercial keys; IBKR and Robinhood listed read-only | Adds a third party in the order path (latency, availability, data sharing) | P3: breadth for brokers without a direct adapter |
| **Robinhood** | No official equities API | **No** — read-only via aggregator; alert-only execution | Unofficial clients prohibited by ToS and liable to lockout; not used | P3: read-only + alert mode |
| **Alert-only adapter** | Push notification / Telegram bot to the owner | Human executes | Universal fallback for any account without a compliant trading API | P1 (built alongside Alpaca) |

New brokers are added by implementing the adapter, passing the contract test suite (§16) against the broker's paper/sandbox or with a zero-capital dry run, then going through the capital ladder per account.

### 13.3 Account registry and router

Each connected account is a registry record: broker, account type (cash/margin/IRA), owner, enabled flag, allocation, per-account limits, supported sessions, and settled-cash rules. The router splits each approved trade across enabled accounts per allocation and enforces **aggregate exposure limits per symbol across all accounts**, so connecting more accounts never multiplies risk on one ticker. Per-account reconciliation runs at startup and every minute; any position or cash mismatch between our ledger and the broker halts that account and alerts.

### 13.3a Connector specifications

Each connector is a `BrokerAdapter` (`src/sgt/core/interfaces.py`) and must pass the shared contract suite (`tests/brokers/test_contract.py`) before it may be enabled for any account. Credentials are read only through `sgt.core.secrets` (OS keychain service `sgt`, or `SGT_SECRET_<NAME>` env in CI) — never `.env`, never the repo.

| Connector | Auth | Secret names | Order path | Fill path | Sessions | Build status |
|---|---|---|---|---|---|---|
| SimBroker | none | — | in-process | in-process | all | Implemented (backtest/shadow) |
| Alpaca (paper) | API key pair | `alpaca_key_id`, `alpaca_secret_key` | `POST /v2/orders` (idempotent via `client_order_id` lookup) | `GET /v2/account/activities/FILL` cursor | pre/rth/post (limit only outside RTH) | Implemented |
| Alpaca (live) | API key pair | `alpaca_live_key_id`, `alpaca_live_secret_key` | same | same | same | Implemented; enabled only via capital ladder |
| Alert-only | none (notifier) | optional webhook URL in settings | human executes from push alert | operator records manual fill via control plane | all | Implemented (Robinhood and any non-API account) |
| Interactive Brokers | IB Gateway session | `ibkr_gateway_host`, `ibkr_client_id` | `ib_async` placeOrder with `orderRef` = client id | execDetails stream | all | Phase 9 — needs a running gateway + paper account to verify |
| Charles Schwab | OAuth2 (individual Trader API) | `schwab_app_key`, `schwab_app_secret`, `schwab_refresh_token` | `POST /trader/v1/accounts/{hash}/orders` | order status polling | rth (+ ext. per order session) | Phase 9 — needs approved developer app; weekly re-auth reminder job |
| Webull | OpenAPI app key/secret | `webull_app_key`, `webull_app_secret` | OpenAPI HTTP trading | gRPC order events | per Webull docs | Phase 9 — verify US individual access first |
| SnapTrade | partner + user secret | `snaptrade_client_id`, `snaptrade_consumer_key`, `snaptrade_user_secret` | unified trade endpoint | account activities | per underlying broker | Phase 9 — E*TRADE/tastytrade/Public/Moomoo breadth |
| Robinhood | — | — | not supported (no official equities API) | read-only via SnapTrade | — | Read-only + alert-only by design |

Deferred connectors are deferred because their contract cannot be verified without live credentials or approvals; shipping an unverified order path would violate the fail-closed rule. Each is a self-contained adapter module plus a contract-suite parametrization, so adding one touches no strategy, engine or risk code.

### 13.4 Order policy

Marketable-limit orders only on low-float names (limit = ask + offset capped by the slippage budget). Extended hours: limit orders, client-side exit management where the broker doesn't support stops. Broker-side protective stops wherever supported, so protection survives our process dying. Before every order, the vendor's last price is cross-checked against the broker's quote; divergence beyond a threshold rejects the order.

---

## 14. Security

| Area | Control |
|---|---|
| Secrets | API keys and OAuth tokens in the OS keychain or a secret manager, never in `.env` in the repo; encrypted at rest; loaded only by the process that needs them |
| Least privilege | Trading-only API scopes; withdrawal/transfer permissions never granted; IP allowlisting where offered; separate paper and live credentials |
| Live arming | Live credentials are loaded only when the master switch is armed by an authenticated operator; default state on any restart is **disarmed** |
| Control plane | Dashboard/API bound to localhost or behind authenticated access (no public exposure); all state-changing actions require auth and are audit-logged |
| Audit | Append-only event log of decisions, orders, config changes, switch toggles, re-auths and guardrail trips; retained indefinitely |
| Untrusted input | Crowd text, vendor payloads and broker responses are parsed strictly with size/time bounds; never interpolated into shell/SQL/prompts |
| Dependencies | Pinned versions; official SDKs preferred; no unofficial broker clients |

---

## 15. Compliance and licensing notes

Market data is licensed at non-professional rates only while the system trades the owner's personal accounts. Trading company money or other people's accounts changes data licensing and may create regulatory obligations — that requires a separate review before it happens. Broker API terms are reviewed per connector. Telegram/StockTwits/Reddit collection follows each platform's terms. The system never posts, promotes, or coordinates trading activity on any platform.

---

## 16. Testing strategy

| Layer | Tests |
|---|---|
| Indicators | Golden tests vs Webull values (2026-10-12 session); incremental == batch property tests; warm-up behavior |
| Leakage | Extend `tests/test_leakage.py` to intraday: shuffled-future-data test, `ingested_at` enforcement |
| Strategy grammar | Schema validation, family/role rule rejection cases, config hashing stability |
| Engine | Determinism (byte-identical logs); replay-equals-live; halt handling; session flatten; extended-hours order-type rejection |
| Cost model | Calibration test against paper/live slippage once available |
| Risk | Every guardrail has a test proving it blocks an order; fail-closed tests for feed loss, broker disconnect, token expiry, reconciliation mismatch |
| Broker contracts | Shared suite every adapter must pass: submit/cancel/fill normalization, idempotent resubmit, reject handling, partial fills — run against paper/sandbox |
| Chaos | Kill the process mid-position; drop the feed; duplicate fill events; out-of-order messages |
| Learning | Label correctness on synthetic paths; promotion gate rejects a model that only beats in-sample |

---

## 17. Phased delivery

Effort estimates assume one engineer working with Claude; they are planning figures, not commitments.

| Phase | Scope | Exit criteria | Est. effort |
|---|---|---|---|
| **0 Decisions & capture** | Confirm open decisions (§19); capture 2026-10-12 1-min bars for the trader's example tickers; open Alpaca free account; create `.claude/work` tracking | Decisions recorded; example-day data stored | 1–2 days |
| **1 Intraday data foundation** | Data audit (§4.2); Parquet store; bar builder; session calendar; halts + EDGAR collectors; float derivation; Telegram collector + parser | Audit finding published; 6–12 months of low-float 1-min history stored with known gaps; Telegram history ingested | 2–3 weeks |
| **1.5 Alert event study** | Forward-return study of Telegram alerts | Written finding: which alert types/contexts have edge, alert latency | 3–5 days |
| **2 Indicator library** | All indicators in §6, incremental + batch, golden tests | Golden tests pass against Webull values | 1–2 weeks |
| **3 Engine + trader_v0** | Event loop, SimBroker, cost model, state machine, trader_v0 config, benchmark reports | First honest answer: does trader_v0 have edge after costs on all crosses? Determinism and replay tests pass | 2–3 weeks |
| **4 Grammar + registry** | Strategy schema, validator, config hashing, registry, trials table | Arbitrary grammar-valid strategies run from YAML | 1–2 weeks |
| **5 Search + validation** | Combination/parameter search, walk-forward, CPCV, deflated Sharpe, plateau selection, lockbox | Ranked candidate cards with confidence intervals; lockbox opened once per finalist | 2–3 weeks |
| **6 Meta-filter** | Triple-barrier labels, LR → LightGBM, calibration, explanations, promotion gate | Meta-filter beats trader_v0 out of sample net of costs, or a documented negative result | 2 weeks |
| **7 Shadow + risk + alert-only** | Delayed-SIP shadow mode; full risk layer; loss budgets; decay monitors; alert-only adapter; dashboard + master switch; monitoring | ≥ 4 weeks of shadow meeting §1 criteria; every guardrail test green | 3–4 weeks build + 4 weeks soak |
| **8 Paper + first live broker** | Paid real-time feed; Alpaca adapter (paper → live); account registry/router; reconciliation | Contract suite green; ≥ 2 weeks paper; then capital ladder stage 1 | 2–3 weeks build + soak |
| **9 Multi-broker** | IBKR, Schwab, Webull (after verification), SnapTrade adapters; Robinhood read-only | Each adapter passes contracts and enters ladder stage 1 independently | 1–2 weeks per broker |
| **10 Continuous improvement** | Weekly analysis reports; challenger pipeline; cost-model recalibration; order-book features (paid data) | Running cadence; every change traceable to evidence | Ongoing |

Critical path: 0 → 1 → 2 → 3 is the go/no-go checkpoint. If trader_v0 and its best variants show no edge after realistic costs at Phase 5, we stop and re-plan rather than build execution infrastructure for a strategy that doesn't work.

---

## 18. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Free historical data lacks delisted names → inflated backtests | High | High | Data audit; survivorship warnings; paid history before any capital-ladder promotion beyond stage 1 |
| Overfitting through combination search | High | High | §10 controls; deflated Sharpe; lockbox; shadow soak |
| Slippage on low-float names exceeds the 1%/day target | High | High | Liquidity-scaled cost model; stricter live universe first; slippage-drift kill |
| Strategy decay after a good run | Medium | High | §11.4 monitors with automatic demotion |
| Broker API changes / auth lapses (Schwab weekly token) | Medium | Medium | Health checks, advance re-auth alerts, fail-closed adapters, broker-side stops |
| Ramp-and-dump names in the alert stream | High | High | Dilution and ramp-risk filters; no-news penalty; position caps per name |
| Process crash with open positions | Low | High | Broker-side stops; startup reconciliation; disarmed-on-restart default |
| Crowd source ToS violation | Low | Medium | Read-only official/permitted access only; per-source terms review |
| Scope creep into heavy ML | Medium | Medium | Hard lightweight budget (§9.1) |

---

## 19. Open decisions

1. **Horizon:** keep the daily/WSB track and add intraday as a second track (recommended), or pivot fully to intraday.
2. **Learning loss budget:** total dollars the owner accepts losing at capital-ladder stages 1–2 before a mandatory review (suggest $500).
3. **Paid data escalation trigger:** move to paid real-time data at Phase 8 (required for live) and to paid history if the Phase 1 audit fails (recommended).
4. **First live broker:** Alpaca (recommended for speed) vs Schwab or Webull because funds already sit there.
5. **Accounts in scope:** list each account (broker, type, owner). All must be the owner's personal accounts unless §15 review is completed.
6. **Telegram access:** confirm subscription to the alert channel(s) and available message history depth.
7. **Dashboard/alert channel:** where alerts go (push notification, personal Telegram bot, email).

---

## Sources consulted (2026-10-12)

- Webull OpenAPI docs: https://developer.webull.com/apis/docs/about-open-api
- Schwab Trader API overview: https://blog.traderspost.io/article/thinkorswim-api ; https://wealth-lab.com/extension/detail/Schwab
- Alpaca data plans: https://alpaca.markets/blog/stock-market-data-api/ ; https://pricingsaas.com/companies/alpaca ; https://forum.alpaca.markets/t/subscription-issue-with-retrieving-historical-results/13482
- Massive (Polygon) pricing: https://apicostcalc.com/blog/polygon-massive-rebrand-api-pricing.html
- Databento: https://databento.com/blog/introducing-databento-us-equities
- SnapTrade broker access: https://docs.snaptrade.com/docs/broker-access-guide
- Robinhood API status: https://www.fintegrationfs.com/fintechapisusa/robinhood-api
- PDT rule elimination: https://international.schwab.com/story/sec-approves-scrapping-25000-day-trader-minimum ; https://us.etrade.com/knowledge/library/margin/pattern-day-trading-rule-change

Third-party claims above (pricing, history depth, broker capabilities) must be re-verified against each vendor's official documentation at the start of the phase that depends on them.
