"""Populate the tradable ticker universe (SEC list, with seed fallback)."""
from sgt.db.session import init_db
from sgt.ingestion.universe import UniverseLoader

if __name__ == "__main__":
    init_db()
    n = UniverseLoader().load()
    print(f"Loaded {n} tickers into universe.")
