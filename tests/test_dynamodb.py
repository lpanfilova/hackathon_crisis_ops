"""DynamoDB mode, exercised with moto so no real AWS account is called.

The process is either sqlite or dynamodb. These tests set DATABASE_BACKEND
for the duration of the fixture and clear the settings cache afterward so the
rest of the suite stays on SQLite.
"""

import pytest
from botocore.exceptions import NoCredentialsError
from fastapi.testclient import TestClient
from moto import mock_aws

from backend.config import DatabaseSettings, get_database_settings
from backend.database import DatabaseUnavailable
from backend.dynamodb_store import get_dynamo_store


@pytest.fixture
def dynamo(monkeypatch):
    monkeypatch.setenv("DATABASE_BACKEND", "dynamodb")
    monkeypatch.setenv("DYNAMODB_TABLE", "crisisops-test")
    monkeypatch.setenv("DYNAMODB_SCENARIO", "bay-county")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    get_database_settings.cache_clear()
    get_dynamo_store.cache_clear()
    with mock_aws():
        yield get_dynamo_store()
    get_dynamo_store.cache_clear()
    get_database_settings.cache_clear()


def test_database_backend_defaults_to_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_BACKEND", raising=False)
    settings = DatabaseSettings(_env_file=None)
    assert settings.database_backend == "sqlite"
    assert settings.dynamodb_table == "crisisops"


def test_missing_credentials_do_not_fall_back_to_sqlite(monkeypatch):
    monkeypatch.setenv("DATABASE_BACKEND", "dynamodb")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    get_database_settings.cache_clear()
    get_dynamo_store.cache_clear()

    def _no_credentials(*_args, **_kwargs):
        raise NoCredentialsError()

    def _sqlite_opened(*_args, **_kwargs):
        raise AssertionError("sqlite opened")

    monkeypatch.setattr("backend.dynamodb_store.boto3.resource", _no_credentials)
    monkeypatch.setattr("init_db.connect", _sqlite_opened)
    try:
        with pytest.raises(DatabaseUnavailable, match="will not fall back to SQLite"):
            get_dynamo_store().ensure_ready()
    finally:
        get_dynamo_store.cache_clear()
        get_database_settings.cache_clear()


def test_reset_loads_the_demo_and_a_second_reset_clears_transactions(dynamo):
    dynamo.reset()
    assert len(dynamo.grouped()["warehouses"]) == 4
    assert dynamo.warehouse_stock("W1") == 300
    assert dynamo.transaction_count() == 0
    dynamo.apply_operation(
        {"action": "decrease_stock", "warehouse": "A", "quantity": 40},
        "We lost 40 food kits to flooding at Warehouse A.",
    )
    assert dynamo.warehouse_stock("W1") == 260
    assert dynamo.transaction_count() == 1
    dynamo.reset()
    assert dynamo.warehouse_stock("W1") == 300
    assert dynamo.transaction_count() == 0


def test_negative_stock_writes_neither_the_balance_nor_a_transaction(dynamo):
    dynamo.reset()
    with pytest.raises(ValueError, match="negative"):
        dynamo.apply_operation(
            {"action": "decrease_stock", "warehouse": "W1", "quantity": 100000},
            "too many",
        )
    assert dynamo.warehouse_stock("W1") == 300
    assert dynamo.transaction_count() == 0


def test_small_seed_can_be_loaded(dynamo):
    dynamo.reset(scenario=None)
    assert dynamo.warehouse_stock("W1") == 100
    assert len(dynamo.grouped()["shelters"]) == 3


def test_server_refuses_to_start_when_the_table_is_missing(dynamo):
    from backend.api import app

    with pytest.raises(DatabaseUnavailable, match="does not exist"):
        with TestClient(app):
            pass


def test_apply_and_preview_use_dynamodb_only(dynamo):
    from backend.api import app

    dynamo.reset()
    with TestClient(app) as client:
        preview = client.post("/plan/preview", json={
            "text": "Warehouse B lost power and stopped shipping.",
            "operation": {"action": "warehouse_offline", "warehouse": "B"},
            "method": "greedy",
        })
        assert preview.status_code == 200
        assert preview.json()["saved"] is False
        assert preview.json()["operation"]["warehouse"] == "W2"
        assert dynamo.transaction_count() == 0
        assert dynamo.grouped()["warehouses"]
        assert next(row["is_operational"] for row in dynamo.grouped()["warehouses"] if row["id"] == "W2")

        state = client.get("/state")
        assert state.status_code == 200
        assert state.json()["summary"]["available_kits"] == 880
        assert state.json()["summary"]["warehouses"] == 4

        applied = client.post("/incidents/apply", json={
            "text": "We lost 40 food kits to flooding at Warehouse A.",
            "operation": {"action": "decrease_stock", "warehouse": "A", "quantity": 40},
            "method": "greedy",
        })
        assert applied.status_code == 200
        body = applied.json()
        assert body["operation"]["warehouse"] == "W1"
        assert body["plans"]["greedy"]["summary"]["total_demand"] == 900
        assert dynamo.warehouse_stock("W1") == 260
        assert dynamo.transaction_count() == 1

        rejected = client.post("/incidents/apply", json={
            "text": "Lost more kits than the warehouse holds.",
            "operation": {"action": "decrease_stock", "warehouse": "W1", "quantity": 100000},
            "method": "greedy",
        })
        assert rejected.status_code == 422
        assert dynamo.warehouse_stock("W1") == 260
        assert dynamo.transaction_count() == 1
