"""Indicator tests are pure (no DB, no network). Shadow the repo-wide autouse
``fresh_db`` fixture with a no-op so these tests neither require nor contend on
the shared file-backed test SQLite database."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def fresh_db():  # noqa: D401 - overrides tests/conftest.py::fresh_db for this package
    yield
