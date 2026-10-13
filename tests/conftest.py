"""Test fixtures. Runs on a throwaway file-backed SQLite DB — no network, no
API keys, no ML deps (sentiment falls back to the lexicon backend)."""
from __future__ import annotations

import os
import tempfile

# Must be set BEFORE any sgt import triggers settings/engine construction.
# Per-process DB/data dir: concurrent pytest runs must never share a SQLite file.
_TMP_ROOT = tempfile.mkdtemp(prefix=f"sgt_test_{os.getpid()}_")
_TMP_DB = os.path.join(_TMP_ROOT, "sgt_test.db")
os.environ["SGT_DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ["SGT_SENTIMENT_BACKEND"] = "lexicon"
os.environ["SGT_AUTHOR_HASH_SALT"] = "test-salt"
os.environ["SGT_COST_BPS"] = "10"
# Never touch the developer's real OS keychain from tests.
os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
os.environ["SGT_DATA_DIR"] = os.path.join(_TMP_ROOT, "data")
os.environ["SGT_EVENT_LOG_DIR"] = os.path.join(_TMP_ROOT, "data", "eventlog")

import pytest  # noqa: E402

from sgt.db.models import Base  # noqa: E402
from sgt.db.session import get_engine, init_db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    engine = get_engine()
    Base.metadata.drop_all(engine)
    init_db()
    yield
    Base.metadata.drop_all(engine)
