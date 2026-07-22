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

## Test

```bash
pytest
```

Tests run on in-memory SQLite and require no network or API keys. The leakage firewall (`tests/test_leakage.py`) is the load-bearing test — it asserts no feature row can see future data.

> Research tool. Not investment advice. `yfinance` is unofficial and unsuitable for anything distributed — swap in a licensed provider before external use.
