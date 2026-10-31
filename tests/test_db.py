"""Tests for database URL resolution."""

import pytest

import quantgpt.db as db


@pytest.mark.parametrize("configured", [None, ""])
def test_missing_or_empty_database_url_uses_sqlite(monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", configured)
    monkeypatch.setattr(db, "_engine", None)

    assert db._get_engine().url.get_backend_name() == "sqlite"
