# Statistical-Grounded Trader — Base Platform Spec

**Status:** Draft v0.1 — foundation ("the base")
**Owners:** Rushil (modeling/architecture), Neel (product)
**Scope of this document:** what we build *first*. The multi-head attention transformer is deliberately out of scope for the base and is specified only as a downstream milestone. The base exists to make the transformer — or any model — trainable, evaluable, and trustworthy.

---

## 1. Context

We are building a stock-signal platform that fuses retail social sentiment (starting with r/WallStreetBets) with market price data and produces short-horizon directional/volatility signals per ticker. The reference point is the "losingloonies" sponsorware, whose pipeline is a WSB mention-momentum scraper feeding a vanilla LSTM. That approach conflates *mention volume* with *signal* and, from what is visible, lacks point-in-time discipline and a costed backtest.

Our differentiator is not a fancier model. It is a **point-in-time-correct feature pipeline** and an **honest evaluation harness**, so that whatever model we put on top (logistic baseline first, transformer later) is measured against reality — transaction costs, slippage, and a naive baseline it must beat — rather than against a leaky in-sample accuracy number.

## 2. Reality check — assumptions we are explicitly rejecting

These are the failure modes that make 95% of "predict stocks from Reddit" projects worthless. The base is designed around avoiding them.

| Weak assumption (common) | Our stance |
|---|---|
| "Sentiment predicts returns." | Sentiment predicts **short-horizon retail-driven momentum and volatility on high-attention tickers**, weakly, and mostly on names WSB actually moves. We target that, not broad alpha. |
| "Accuracy is the metric." | Directional accuracy near the base rate is noise. We measure hit-rate *vs. base rate*, precision on the traded subset, and **PnL after costs + Sharpe** in a walk-forward backtest. |
| "More model = more signal." | Transformer will overfit our data volume before a baseline is beaten. Every model must beat: (a) majority class, (b) logistic regression on engineered features. No baseline beaten → model is rejected. |
| "Scrape whenever, join on ticker." | **Look-ahead leakage kills these projects.** Every feature at time *t* uses only data timestamped `< t`. Labels are computed on the *next* bar. This is enforced in the feature store, not left to convention. |
| "Pushshift for history." | Pushshift public access is effectively dead. We use the official Reddit API (PRAW) going forward and treat historical backfill as a separate, licensed problem. |
| "Cashtag regex = ticker." | `$A`, `IT`, `FOR`, `DD`, `CEO` are false positives. Ticker extraction validates against a maintained ticker universe + a stopword blocklist. |

If, after the base is built, the signal does not beat baselines net of costs, that is a **finding, not a failure** — and the platform is built to surface it rather than hide it.

## 3. Prediction target (must be fixed before any modeling)

The base commits to one primary target so features and labels are unambiguous:

- **Universe:** tickers with ≥ N WSB mentions in the trailing 24h (N configurable, default 25), intersected with a liquid equities/ETF list. Meme-stock-dominated by construction — that is where the signal, if any, lives.
- **Horizon:** next-day (close-to-close) to start; intraday is a later extension.
- **Label (primary):** sign of next-bar return net of a cost threshold — `up` if `next_return > +τ`, `down` if `< −τ`, else `flat` (τ ≈ round-trip cost, default 10 bps). Three-class avoids rewarding the model for calling direction inside the noise band.
- **Label (secondary, tracked):** next-bar realized volatility bucket. Sentiment spikes predict volatility more reliably than direction; we log this even in the base.

Every stored feature row is keyed `(ticker, as_of_timestamp)` and every label is keyed to the bar that closes *after* `as_of_timestamp`.

## 4. Architecture (end to end)

```
                 ┌────────────────────────────────────────────────┐
                 │                 Ingestion                       │
  Reddit API ───▶│ reddit_collector (PRAW, scheduled)              │
  (r/wsb)        │ market_collector (yfinance/Polygon, scheduled)  │───┐
                 └────────────────────────────────────────────────┘   │
                                                                       ▼
                 ┌────────────────────────────────────────────────────────────┐
   Postgres ◀────│  raw layer: posts, comments, ohlcv (append-only, tz-aware)  │
  (Supabase)     └────────────────────────────────────────────────────────────┘
                                                                       │
                 ┌─────────────────────────────────────────────────────▼──────┐
                 │  processing: ticker_extraction → sentiment (FinBERT) →      │
                 │  time-bucketed aggregation                                  │
                 └─────────────────────────────────────────────────────┬──────┘
                                                                        ▼
                 ┌────────────────────────────────────────────────────────────┐
                 │  feature store: point-in-time feature rows + labels         │
                 └─────────────────────────────────────────────────────┬──────┘
                                                                        ▼
        ┌──────────────────────────┐        ┌──────────────────────────────────┐
        │ modeling: baselines →     │◀──────▶│ backtest harness: walk-forward,   │
        │ (later) transformer       │        │ costs, slippage, metrics, reports │
        └──────────────┬────────────┘        └──────────────────────────────────┘
                       ▼
        ┌──────────────────────────┐
        │ serving: FastAPI /signals │  (frontend dashboard = later milestone)
        └──────────────────────────┘
```

## 5. Component specs

**Ingestion — `reddit_collector`.** Uses PRAW against the official Reddit API (requires a registered app: client id/secret/user-agent in env, never committed). Pulls new submissions + comments from r/WallStreetBets on a schedule (default every 15 min). Stores raw text, author, score, `created_utc` (UTC, tz-aware), permalink, and a stable Reddit id for idempotent upserts. Rate-limit aware; backs off on 429. No PII beyond the public Reddit username; usernames are hashed at rest.

**Ingestion — `market_collector`.** yfinance for the MVP (free, sufficient for daily OHLCV), abstracted behind a `MarketDataProvider` interface so Polygon/Alpaca can be swapped in for intraday + reliability without touching downstream code. Stores adjusted OHLCV per ticker/day. Corporate actions (splits/dividends) handled via adjusted close.

**Processing — ticker extraction.** Regex candidates (`$TICK`, uppercase tokens) → validated against a `ticker_universe` table (refreshed from an exchange listing) → filtered by a stopword blocklist (`DD`, `IT`, `CEO`, `YOLO`, `FD`, …). Emits `(post_id, ticker, position)` mentions. Precision over recall: a missed mention is cheaper than a poisoned feature.

**Processing — sentiment.** `ProsusAI/finbert` (finance-domain, 3-class pos/neg/neutral) run per mention-bearing text span. Store the class + softmax scores, not just the argmax, so aggregation can be probability-weighted. Model version pinned and recorded on every row for reproducibility.

**Processing — aggregation.** Roll mentions + sentiment into `(ticker, time_bucket)` features: mention count, unique authors, score-weighted mention count, mean/net sentiment, sentiment dispersion, and deltas vs. trailing windows (1h/6h/24h). Buckets are closed intervals; a bucket labeled `t` contains only events with `created_utc` inside it.

**Feature store.** The leakage firewall. A feature row `(ticker, as_of_ts)` is materialized only from raw data with `timestamp < as_of_ts`. Labels join the *next* trading bar. Provides a single `get_training_frame(start, end)` that guarantees no future data leaks — this is the only sanctioned way models get data.

**Modeling.** Baselines first (§8). Common `fit/predict` interface + a model registry recording feature set, hyperparams, code hash, and eval metrics per trained artifact.

**Backtest harness (§7).** The most important component. Walk-forward only.

**Serving.** FastAPI: `GET /signals?date=` returns per-ticker signal + confidence + the top contributing features (explainability is a product requirement, not a nicety). Read-only over the model registry's latest promoted artifact.

## 6. Data model (Postgres)

| Table | Key columns | Notes |
|---|---|---|
| `posts` | `reddit_id` PK, `created_utc`, `author_hash`, `score`, `body`, `permalink` | append-only, idempotent upsert |
| `comments` | `reddit_id` PK, `post_id` FK, `created_utc`, `author_hash`, `score`, `body` | " |
| `ohlcv` | (`ticker`,`date`) PK, `open/high/low/close/adj_close/volume`, `provider` | adjusted close authoritative |
| `ticker_universe` | `ticker` PK, `name`, `exchange`, `active` | refreshed weekly |
| `mentions` | (`source_id`,`ticker`) , `created_utc`, `sentiment_label`, `sentiment_scores` jsonb, `model_version` | one row per (text, ticker) |
| `features` | (`ticker`,`as_of_ts`) PK, feature columns…, `feature_set_version` | point-in-time; never updated in place |
| `labels` | (`ticker`,`as_of_ts`) PK, `fwd_return`, `class`, `vol_bucket` | joined to next bar |
| `model_registry` | `model_id` PK, `code_hash`, `feature_set_version`, `metrics` jsonb, `promoted` bool | reproducibility |

All timestamps `timestamptz`, stored UTC. Migrations via Supabase migrations (MCP available) or Alembic.

## 7. Backtest & evaluation harness (non-negotiable design)

- **Walk-forward:** train on `[t0, t1)`, test on `[t1, t2)`, roll forward. No random shuffled split — ever.
- **Costs:** configurable commission + spread + slippage; default round-trip 10 bps. Signals inside the cost band are not traded.
- **Metrics:** hit-rate **vs. base rate**; precision/recall per class on the traded subset; cumulative PnL net of costs; Sharpe; max drawdown; turnover. Accuracy alone is banned from reports.
- **Baseline gate:** every candidate model is reported side-by-side with majority-class and logistic-regression baselines. Promotion to `serving` requires beating both **net of costs**.
- **Report artifact:** each run emits a versioned report (metrics + equity curve + config + code hash) so results are auditable and reproducible.

## 8. Model roadmap

1. **Majority class** — sanity floor.
2. **Logistic regression / gradient boosting** on §5 features — the real bar to clear; interpretable, cheap, hard to beat with small data.
3. **LSTM** — parity check against the competitor's class of model.
4. **Multi-head attention transformer** (Rushil's design) — sequence of per-ticker time-bucketed feature vectors → attention over the lookback window + cross-ticker attention for market-regime context → 3-class head + auxiliary volatility head. Built **only after** the harness shows the feature signal beats baselines, so we are tuning a real signal rather than fitting noise with more parameters.

## 9. Legal / compliance / ops

- **Reddit:** official API + registered app; respect rate limits and Reddit API Terms. Store only public fields; hash usernames.
- **Market data:** yfinance is best-effort/unofficial — fine for research, **not** for anything sold. A licensed provider (Polygon/Alpaca) is required before any external distribution.
- **Not investment advice:** every signal surface carries a disclaimer. This is a research tool. No brokerage/auto-trading in the base.
- **Secrets:** all keys via env / secret manager; `.env` git-ignored; nothing committed. (Reinforces repo policy.)
- **Reproducibility:** code hash + model version + feature-set version stamped on every artifact and prediction.

## 10. Repo layout

```
statistical-grounded-trader/
├── docs/SPEC.md
├── pyproject.toml
├── .env.example
├── src/sgt/
│   ├── ingestion/   reddit_collector.py  market_collector.py
│   ├── processing/  ticker_extraction.py sentiment.py aggregation.py
│   ├── features/    store.py  schema.py
│   ├── models/      baselines.py registry.py
│   ├── backtest/    harness.py metrics.py
│   ├── api/         app.py
│   └── db/          migrations/  session.py
├── tests/
└── scripts/         run_ingestion.py  build_features.py  backtest.py
```

## 11. What to implement next (Phase 1 backlog — the base)

Ordered. Each item has an acceptance criterion. Phase 1 stops *before* any transformer.

| # | Task | Acceptance criterion |
|---|---|---|
| 1 | Repo scaffold + `pyproject`, env config, DB session, Supabase schema migration (§6 tables) | `pytest` runs; migration applies; `.env.example` present |
| 2 | `reddit_collector` (PRAW) | Scheduled run persists de-duplicated posts+comments with tz-aware UTC timestamps; idempotent on re-run |
| 3 | `market_collector` behind `MarketDataProvider` (yfinance impl) | Daily adjusted OHLCV for the active universe stored; provider swappable |
| 4 | `ticker_universe` loader + ticker extraction + stopword blocklist | On a labeled sample, extraction precision ≥ 0.95; known false positives (`IT`,`DD`,`CEO`) excluded |
| 5 | FinBERT sentiment, pinned version, scores persisted | `mentions` rows carry class + softmax + `model_version` |
| 6 | Time-bucketed aggregation → `features` | Feature row for `(ticker, t)` provably uses only `timestamp < t` (leakage unit test passes) |
| 7 | Label builder → `labels` (3-class + vol bucket, τ configurable) | Labels join strictly to the next bar; no same-bar leakage |
| 8 | `get_training_frame()` point-in-time join | Test asserting zero future rows in any training frame |
| 9 | Backtest harness: walk-forward + costs + baseline gate + report | Report emitted with hit-rate-vs-base-rate, net PnL, Sharpe; majority + logistic baselines included |
| 10 | Baseline models (logistic / gradient boosting) | Trained via registry; metrics recorded; result reported honestly whether or not it beats base rate |
| 11 | FastAPI `GET /signals` over promoted artifact | Returns signal + confidence + top features for a date |

**Phase 1 exit gate:** the harness produces a reproducible walk-forward report for a baseline model, net of costs, against the base rate. Only then do we start the transformer (Milestone 2). If the feature signal is not there, we learn it here — cheaply — instead of after burning weeks on attention layers.

## 12. Open questions (need Rushil/Neel decisions)

1. **History vs. go-forward:** buy historical WSB data (licensed) for a bigger backtest, or start collecting now and accept a short backtest window for months? (Recommend: start collecting now, revisit history after Phase 1 shows signal.)
2. **Universe size:** meme-only (high signal, few names) vs. broader liquid universe (more data, weaker signal)? Default meme-biased via mention threshold.
3. **Market-data budget:** stay on free yfinance for research, or provision Polygon/Alpaca now for intraday? (Recommend: yfinance until Phase 1 gate.)
4. **Compute:** where does FinBERT + training run — local, or a GPU box? Affects ingestion throughput ceiling.
