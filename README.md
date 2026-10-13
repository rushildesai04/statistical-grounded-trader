# statistical-grounded-trader

Social + market data pipeline with point-in-time-correct features and a walk-forward backtest harness. Design and rationale: [docs/SPEC.md](docs/SPEC.md).

The base fuses retail social sentiment (r/WallStreetBets and other subs, StockTwits) with market data (OHLCV, VIX, news) into leakage-free feature rows, then evaluates any model against costed baselines. Model training is a downstream milestone — the base exists to make it trustworthy.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # add ,ml or install .[ml] for FinBERT
cp .env.example .env           # fill in Reddit API creds; SQLite works out of the box
python scripts/init_db.py      # create tables
```

## Run the pipeline

```bash
python scripts/load_universe.py            # populate tradable ticker list
python scripts/run_ingestion.py            # collect Reddit + StockTwits + market + news
python scripts/build_features.py           # ticker extraction -> sentiment -> features + labels
python scripts/backtest.py                 # walk-forward report vs baselines, net of costs
uvicorn sgt.api.app:app --reload           # GET /signals?date=YYYY-MM-DD
```

## Intraday signal engine (1-minute track)

Plan and rationale: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md). Operations: [docs/runbooks/intraday_operations.md](docs/runbooks/intraday_operations.md).

One engine runs in four modes — `backtest`, `shadow`, `paper`, `live` — so backtest and live decisions come from identical code. Strategies are declarative YAML validated by a signal-family grammar (`strategies/trader_v0.yaml` encodes the reference trader's EMA 9/24 + volume + PVT/PSAR rules).

```bash
pip install -e ".[dev,crowd]"
python scripts/capture_session.py --symbols WFF,YMAT --date 2026-10-12 --source yfinance   # 1-min bars -> data/
python scripts/data_audit.py --source yfinance --symbols WFF,YMAT --start 2026-10-05 --end 2026-10-12
python scripts/backtest_intraday.py --strategy strategies/trader_v0.yaml --symbols WFF,YMAT --start 2026-10-05 --end 2026-10-12
python scripts/search.py --space strategies/search/trader_v0_space.yaml --start 2026-07-01 --end 2026-10-12 --lockbox-start 2026-09-14   # walk-forward, deflated Sharpe, lockbox
python scripts/run_live.py --strategy strategies/trader_v0.yaml --mode shadow --accounts sim --symbols WFF,YMAT --feed delayed_sip
python scripts/run_control_plane.py                                                           # 127.0.0.1 only; arm/disarm, stages, manual fills
```

Secrets (broker keys, control-plane token, Telegram) live in the OS keychain (service `sgt`) or `SGT_SECRET_<NAME>` env vars — never in `.env` or the repo. Live trading starts **disarmed** and requires an explicit arm via the control plane.

## Test

```bash
pytest
```

Tests run on a per-process throwaway SQLite file and require no network or API keys. The leakage firewall (`tests/test_leakage.py`) is the load-bearing test — it asserts no feature row can see future data.

> Research tool. Not investment advice. `yfinance` is unofficial and unsuitable for anything distributed — swap in a licensed provider before external use.
