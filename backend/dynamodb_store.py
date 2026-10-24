"""DynamoDB store for the Bay County world and its confirmed changes.

One table. The partition key is the scenario name (default ``bay-county``).
The sort key is ``warehouse#W1``, ``shelter#S3``, ``truck#T1``,
``route#W4#S3``, or ``txn#<time>#<id>``.

This module is used only when ``DATABASE_BACKEND=dynamodb``. It does not open
SQLite and it does not catch credential errors in order to write somewhere else.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError, NoCredentialsError, PartialCredentialsError

from backend.config import get_allocator_settings, get_database_settings
from backend.database import (
    CREDENTIALS_MESSAGE,
    NOT_INITIALIZED,
    ConstraintError,
    DatabaseNotInitialized,
    DatabaseUnavailable,
    missing_table_message,
)
from init_db import DEFAULT_SCENARIO_PATH, load_scenario, match_id
from optimization.allocator import PlanningParams, allocate
from optimization.scenario import Scenario

_CREDENTIAL_CODES = {
    "UnrecognizedClientException",
    "InvalidClientTokenId",
    "ExpiredTokenException",
    "ExpiredToken",
    "AccessDeniedException",
    "AuthFailure",
    "IncompleteSignatureException",
}

_INCIDENT_TYPE = {
    "increase_stock": "stock_increase",
    "decrease_stock": "stock_decrease",
    "set_stock": "stock_set",
    "warehouse_offline": "warehouse_offline",
    "warehouse_online": "warehouse_online",
    "set_demand": "demand_change",
    "truck_offline": "truck_offline",
    "truck_online": "truck_online",
    "close_route": "route_closed",
    "open_route": "route_open",
}


def _py(value: Any) -> Any:
    """DynamoDB returns numbers as Decimal. JSON and the planner want int/float."""
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    return value


def _to_dynamo(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DynamoStore:
    """The live scenario and its transaction log in one DynamoDB table."""

    def __init__(self, table_name: str, scenario: str, region: str | None):
        self.table_name = table_name
        self.scenario = scenario
        self.region = region or None
        self._resource_obj = None

    def _resource(self):
        if self._resource_obj is None:
            kwargs = {}
            if self.region:
                kwargs["region_name"] = self.region
            try:
                self._resource_obj = boto3.resource("dynamodb", **kwargs)
            except (NoCredentialsError, PartialCredentialsError) as exc:
                raise DatabaseUnavailable(CREDENTIALS_MESSAGE) from exc
        return self._resource_obj

    def _client(self):
        return self._resource().meta.client

    @property
    def table(self):
        return self._resource().Table(self.table_name)

    def _reraise_client(self, exc: ClientError) -> None:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _CREDENTIAL_CODES:
            raise DatabaseUnavailable(CREDENTIALS_MESSAGE) from exc
        if code == "ResourceNotFoundException":
            raise DatabaseUnavailable(missing_table_message(self.table_name)) from exc
        raise exc

    def _describe(self) -> dict[str, Any]:
        try:
            return self._client().describe_table(TableName=self.table_name)
        except (NoCredentialsError, PartialCredentialsError) as exc:
            raise DatabaseUnavailable(CREDENTIALS_MESSAGE) from exc
        except ClientError as exc:
            self._reraise_client(exc)
            raise

    def _wait_active(self) -> None:
        self._client().get_waiter("table_exists").wait(
            TableName=self.table_name,
            WaiterConfig={"Delay": 1, "MaxAttempts": 30},
        )

    def ensure_table(self) -> None:
        """Create the table when it is missing. Used by reset, not by request handlers."""
        try:
            desc = self._describe()
        except DatabaseUnavailable as exc:
            if "does not exist" not in str(exc):
                raise
            self._create_table()
            return
        if desc["Table"]["TableStatus"] != "ACTIVE":
            self._wait_active()

    def _create_table(self) -> None:
        client = self._client()
        try:
            client.create_table(
                TableName=self.table_name,
                KeySchema=[
                    {"AttributeName": "pk", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "pk", "AttributeType": "S"},
                    {"AttributeName": "sk", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
        except client.exceptions.ResourceInUseException:
            pass
        except (NoCredentialsError, PartialCredentialsError) as exc:
            raise DatabaseUnavailable(CREDENTIALS_MESSAGE) from exc
        except ClientError as exc:
            self._reraise_client(exc)
        self._wait_active()

    def ensure_ready(self) -> None:
        """Confirm credentials and the table before serving. Does not load data.

        A missing credential or a missing table raises DatabaseUnavailable.
        An empty table is allowed; data routes then report that reset has not
        been run. This never opens SQLite.
        """
        desc = self._describe()
        if desc["Table"]["TableStatus"] != "ACTIVE":
            self._wait_active()

    def _query_all(self) -> list[dict[str, Any]]:
        # The resource client's paginator returns plain Python values, not
        # AttributeValue dicts. TransactWriteItems below is the low-level
        # call because a condition builder cannot express a multi-item write.
        paginator = self._client().get_paginator("query")
        items: list[dict[str, Any]] = []
        try:
            pages = paginator.paginate(
                TableName=self.table_name,
                KeyConditionExpression="pk = :pk",
                ExpressionAttributeValues={":pk": self.scenario},
            )
            for page in pages:
                items.extend(page.get("Items", []))
        except (NoCredentialsError, PartialCredentialsError) as exc:
            raise DatabaseUnavailable(CREDENTIALS_MESSAGE) from exc
        except ClientError as exc:
            self._reraise_client(exc)
        return items

    def has_warehouses(self) -> bool:
        try:
            response = self.table.query(
                KeyConditionExpression=Key("pk").eq(self.scenario) & Key("sk").begins_with("warehouse#"),
                Limit=1,
                Select="COUNT",
            )
        except (NoCredentialsError, PartialCredentialsError) as exc:
            raise DatabaseUnavailable(CREDENTIALS_MESSAGE) from exc
        except ClientError as exc:
            self._reraise_client(exc)
            raise
        return response["Count"] > 0

    def require_initialized(self) -> None:
        if not self.has_warehouses():
            raise DatabaseNotInitialized(NOT_INITIALIZED)

    def _entity(self, sk: str, entity: str, **fields: Any) -> dict[str, Any]:
        item: dict[str, Any] = {"pk": self.scenario, "sk": sk, "entity": entity}
        for key, value in fields.items():
            if value is None:
                continue
            item[key] = _to_dynamo(value)
        return item

    def _items_from_document(self, data: Mapping[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in data.get("warehouses", []):
            items.append(self._entity(
                f"warehouse#{row['id']}", "warehouse",
                id=row["id"], name=row["name"], location=row["location"],
                usable_stock=int(row["usable_stock"]),
                is_operational=bool(row.get("is_operational", True)),
                x_km=row.get("x_km"), y_km=row.get("y_km"),
            ))
        for row in data.get("shelters", []):
            items.append(self._entity(
                f"shelter#{row['id']}", "shelter",
                id=row["id"], name=row["name"], location=row["location"],
                demand=int(row["demand"]), priority=int(row["priority"]),
                x_km=row.get("x_km"), y_km=row.get("y_km"),
            ))
        for row in data.get("trucks", []):
            items.append(self._entity(
                f"truck#{row['id']}", "truck",
                id=row["id"], warehouse_id=row["warehouse_id"],
                capacity_kits=int(row["capacity_kits"]),
                is_operational=bool(row.get("is_operational", True)),
            ))
        for row in data.get("delivery_routes", []):
            wid, sid = row["warehouse_id"], row["shelter_id"]
            items.append(self._entity(
                f"route#{wid}#{sid}", "route",
                warehouse_id=wid, shelter_id=sid,
                distance_km=float(row["distance_km"]),
                is_open=bool(row.get("is_open", True)),
            ))
        return items

    def _document(self, scenario: Path | str | None) -> dict[str, Any]:
        if scenario is not None:
            return load_scenario(scenario)
        # The small seed is SQL. Materialize it, then copy the rows across.
        # This import is local so the store module does not need sqlite at import
        # except on this path.
        import tempfile

        from init_db import connect, reset_db

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seed.db"
            reset_db(path, scenario=None)
            conn = connect(path)
            try:
                def rows(sql: str) -> list[dict[str, Any]]:
                    return [dict(row) for row in conn.execute(sql)]

                return {
                    "warehouses": rows("SELECT * FROM warehouses;"),
                    "shelters": rows("SELECT * FROM shelters;"),
                    "trucks": rows("SELECT * FROM trucks;"),
                    "delivery_routes": rows(
                        "SELECT warehouse_id, shelter_id, distance_km, is_open FROM delivery_routes;"
                    ),
                }
            finally:
                conn.close()

    def _delete_all(self) -> None:
        items = self._query_all()
        if not items:
            return
        with self.table.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})

    def _put_items(self, items: list[dict[str, Any]]) -> None:
        with self.table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)

    def reset(self, scenario: Path | str | None = DEFAULT_SCENARIO_PATH) -> None:
        """Delete every row in this scenario and write the mock data back."""
        self.ensure_table()
        self._delete_all()
        self._put_items(self._items_from_document(self._document(scenario)))

    def init_if_missing(self, scenario: Path | str | None = DEFAULT_SCENARIO_PATH) -> bool:
        """Load the scenario only when the table has no warehouses. Returns True if it loaded."""
        self.ensure_table()
        if self.has_warehouses():
            return False
        self._put_items(self._items_from_document(self._document(scenario)))
        return True

    def _plain(self, item: Mapping[str, Any]) -> dict[str, Any]:
        skip = {"pk", "sk", "entity"}
        return {key: _py(value) for key, value in item.items() if key not in skip}

    def grouped(self) -> dict[str, list[dict[str, Any]]]:
        self.require_initialized()
        groups: dict[str, list[dict[str, Any]]] = {
            "warehouses": [], "shelters": [], "trucks": [],
            "delivery_routes": [], "transactions": [],
        }
        buckets = {
            "warehouse": "warehouses",
            "shelter": "shelters",
            "truck": "trucks",
            "route": "delivery_routes",
            "txn": "transactions",
        }
        for item in self._query_all():
            kind = str(item.get("entity") or str(item.get("sk", "")).split("#", 1)[0])
            bucket = buckets.get(kind)
            if bucket is None:
                continue
            groups[bucket].append(self._plain(item))
        return groups

    def resolve_operation(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        groups = self.grouped()
        resolved = dict(operation)
        rows = {
            "warehouse": groups["warehouses"],
            "shelter": groups["shelters"],
            "truck": groups["trucks"],
        }
        for field in rows:
            value = resolved.get(field)
            if value is not None and value != "":
                resolved[field] = match_id(field, value, rows[field])
        return resolved

    def _key(self, sk: str) -> dict[str, str]:
        return {"pk": self.scenario, "sk": sk}

    def _get(self, sk: str) -> dict[str, Any] | None:
        found = self.table.get_item(Key=self._key(sk)).get("Item")
        return dict(found) if found else None

    def _transact(self, sk: str, update_expression: str, condition: str, values: dict[str, Any], txn: dict[str, Any]) -> None:
        client = self._client()
        try:
            client.transact_write_items(TransactItems=[
                {
                    "Update": {
                        "TableName": self.table_name,
                        "Key": self._key(sk),
                        "UpdateExpression": update_expression,
                        "ConditionExpression": condition,
                        "ExpressionAttributeValues": values,
                    }
                },
                {"Put": {"TableName": self.table_name, "Item": txn}},
            ])
        except (NoCredentialsError, PartialCredentialsError) as exc:
            raise DatabaseUnavailable(CREDENTIALS_MESSAGE) from exc
        except client.exceptions.TransactionCanceledException as exc:
            self._reraise_cancelled(exc, sk)
        except ClientError as exc:
            self._reraise_client(exc)

    def _reraise_cancelled(self, exc: ClientError, sk: str) -> None:
        reasons = exc.response.get("CancellationReasons") or []
        failed = any(reason.get("Code") == "ConditionalCheckFailed" for reason in reasons)
        if not failed:
            raise exc
        if self._get(sk) is None:
            raise ValueError(f"No row {sk!r} in scenario {self.scenario!r}")
        raise ConstraintError(
            "Operation rejected because stock or demand would become negative."
        )

    def _txn(self, incident_type: str, target_id: str, description: str) -> dict[str, Any]:
        created = _now_iso()
        return self._entity(
            f"txn#{created}#{uuid.uuid4().hex[:8]}",
            "txn",
            incident_type=incident_type,
            target_id=target_id,
            description=description,
            created_at=created,
        )

    def _missing(self, kind: str, identifier: str) -> ValueError:
        if kind == "route":
            warehouse_id, shelter_id = identifier.split("->", 1)
            return ValueError(f"No route for warehouse {warehouse_id!r} -> shelter {shelter_id!r}")
        label = {"warehouse": "warehouse", "shelter": "shelter", "truck": "truck"}[kind]
        return ValueError(f"No {label} with id {identifier!r}")

    def apply_operation(self, operation: Mapping[str, Any], description: str) -> dict[str, Any]:
        """Apply one operation and append its transaction row. Returns resolved ids."""
        resolved = self.resolve_operation(operation)
        action = resolved.get("action")
        if action not in _INCIDENT_TYPE:
            raise ValueError(f"Unknown action {action!r}")

        def need(field: str) -> Any:
            value = resolved.get(field)
            if value is None or value == "":
                raise ValueError(f"Action {action!r} requires {field!r}")
            return value

        def quantity() -> int:
            qty = int(need("quantity"))
            if qty < 0:
                raise ValueError(f"quantity must be >= 0, got {qty}")
            return qty

        incident_type = _INCIDENT_TYPE[action]
        if action in ("increase_stock", "decrease_stock"):
            qty = quantity()
            delta = qty if action == "increase_stock" else -qty
            wid = str(need("warehouse"))
            sk = f"warehouse#{wid}"
            values: dict[str, Any] = {":delta": delta}
            condition = "attribute_exists(pk)"
            if delta < 0:
                values[":need"] = -delta
                condition = "attribute_exists(pk) AND usable_stock >= :need"
            self._write(sk, "SET usable_stock = usable_stock + :delta", condition, values,
                        self._txn(incident_type, wid, description), "warehouse", wid)
        elif action == "set_stock":
            wid = str(need("warehouse"))
            self._write(
                f"warehouse#{wid}", "SET usable_stock = :qty", "attribute_exists(pk)",
                {":qty": quantity()}, self._txn(incident_type, wid, description), "warehouse", wid,
            )
        elif action in ("warehouse_offline", "warehouse_online"):
            wid = str(need("warehouse"))
            self._write(
                f"warehouse#{wid}", "SET is_operational = :flag", "attribute_exists(pk)",
                {":flag": action == "warehouse_online"},
                self._txn(incident_type, wid, description), "warehouse", wid,
            )
        elif action == "set_demand":
            sid = str(need("shelter"))
            self._write(
                f"shelter#{sid}", "SET demand = :qty", "attribute_exists(pk)",
                {":qty": quantity()}, self._txn(incident_type, sid, description), "shelter", sid,
            )
        elif action in ("truck_offline", "truck_online"):
            tid = str(need("truck"))
            self._write(
                f"truck#{tid}", "SET is_operational = :flag", "attribute_exists(pk)",
                {":flag": action == "truck_online"},
                self._txn(incident_type, tid, description), "truck", tid,
            )
        else:
            wid, sid = str(need("warehouse")), str(need("shelter"))
            self._write(
                f"route#{wid}#{sid}", "SET is_open = :flag", "attribute_exists(pk)",
                {":flag": action == "open_route"},
                self._txn(incident_type, f"{wid}->{sid}", description), "route", f"{wid}->{sid}",
            )
        return resolved

    def _write(self, sk: str, expression: str, condition: str, values: dict[str, Any],
               txn: dict[str, Any], kind: str, identifier: str) -> None:
        try:
            self._transact(sk, expression, condition, values, txn)
        except ValueError as exc:
            if str(exc).startswith("No row "):
                raise self._missing(kind, identifier) from exc
            raise

    def scenario_data(self, groups: Mapping[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
        groups = self.grouped() if groups is None else groups
        return {
            "warehouses": groups["warehouses"],
            "shelters": groups["shelters"],
            "trucks": groups["trucks"],
            "delivery_routes": groups["delivery_routes"],
        }

    def network_scenario(self, groups: Mapping[str, list[dict[str, Any]]] | None = None) -> Scenario:
        return Scenario(self.scenario_data(groups))

    def plans(self, method: str | None = None, settings=None) -> dict[str, Any]:
        if settings is None:
            settings = get_allocator_settings()
        return allocate(
            self.network_scenario().network(),
            method or settings.allocator_method,
            PlanningParams.from_settings(settings),
            time_limit_s=settings.optimizer_time_limit_s,
            rounding_time_s=settings.optimizer_rounding_time_s,
        )

    def names(self, groups: Mapping[str, list[dict[str, Any]]] | None = None) -> tuple[dict[str, str], dict[str, str]]:
        groups = self.grouped() if groups is None else groups
        warehouses = {row["id"]: row["name"] for row in groups["warehouses"]}
        shelters = {row["id"]: row["name"] for row in groups["shelters"]}
        return warehouses, shelters

    def transaction_count(self) -> int:
        return sum(1 for item in self._query_all() if str(item.get("sk", "")).startswith("txn#"))

    def warehouse_stock(self, warehouse_id: str) -> int:
        item = self._get(f"warehouse#{warehouse_id}")
        if item is None:
            raise ValueError(f"No warehouse with id {warehouse_id!r}")
        return int(item["usable_stock"])


@lru_cache
def get_dynamo_store() -> DynamoStore:
    """The process-wide DynamoDB store. Cached so the client is reused."""
    settings = get_database_settings()
    return DynamoStore(settings.dynamodb_table, settings.dynamodb_scenario, settings.aws_region)
