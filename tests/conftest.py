"""Keep the default test run on SQLite even when a local .env selects DynamoDB."""

import pytest

from backend.config import get_database_settings
from backend.dynamodb_store import get_dynamo_store


@pytest.fixture(autouse=True)
def sqlite_backend_unless_a_test_selects_dynamodb(monkeypatch):
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")
    get_database_settings.cache_clear()
    get_dynamo_store.cache_clear()
    yield
    get_database_settings.cache_clear()
    get_dynamo_store.cache_clear()
