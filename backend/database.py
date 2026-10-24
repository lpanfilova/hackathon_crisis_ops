"""Which database this process uses.

``DATABASE_BACKEND=sqlite`` (the default) keeps the current SQLite file.
``DATABASE_BACKEND=dynamodb`` uses one DynamoDB table for the mock world and
for every confirmed change. The choice is fixed when the process starts. A
DynamoDB credential or table failure is reported as an error; those requests
are not saved to SQLite instead.
"""

from __future__ import annotations

from fastapi import HTTPException


class DatabaseUnavailable(RuntimeError):
    """DynamoDB mode cannot run. SQLite is not used as a substitute."""


class DatabaseNotInitialized(RuntimeError):
    """The selected database has no scenario loaded yet."""


class ConstraintError(ValueError):
    """The write would make stock or demand negative."""


NOT_INITIALIZED = "Database is not initialized. Run: python init_db.py reset"

CREDENTIALS_MESSAGE = (
    "DynamoDB credentials were not found or were rejected. "
    "This process is set to DynamoDB and will not fall back to SQLite."
)


def missing_table_message(table_name: str) -> str:
    return (
        f"DynamoDB table {table_name!r} does not exist. "
        "Run `python init_db.py reset` to create it and load the demo, "
        "or start this process with DATABASE_BACKEND=sqlite."
    )


def use_dynamodb() -> bool:
    """True when this process is configured to use DynamoDB only."""
    from backend.config import get_database_settings

    return get_database_settings().database_backend == "dynamodb"


def store_status(exc: Exception) -> HTTPException | None:
    """Map a store error onto an HTTP response, when it is one of ours."""
    if isinstance(exc, (DatabaseNotInitialized, DatabaseUnavailable)):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return None
