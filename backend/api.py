"""Backend API routes."""

import sqlite3
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ai.incident_parser import IncidentUpdate, ParseError, parse_incident
from backend.display_api import router as display_router
from backend.frontend_page import router as frontend_router
from init_db import apply_operation, connect, resolve_operation
from optimization.allocator import plan_from_db

app = FastAPI(title="CrisisOps Incident API")

# Lets the standalone frontend (python3 frontend/app.py, port 8000) call this
# API when it runs on another port, e.g. uvicorn backend.api:app --port 8001.
# Browsers treat 127.0.0.1 and localhost as different origins, so list both.
# Not needed when uvicorn serves the page itself (GET /), which is same-origin.
CORS_ORIGINS = ["http://127.0.0.1:8000", "http://localhost:8000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

app.include_router(display_router)  # GET /state, GET /plan, POST /plan/preview
app.include_router(frontend_router)  # GET / serves the dashboard on the API's origin

# One example operation per action. Ids are database ids (W1, S3, T1), the
# same shape ``init_db.apply_operation`` accepts. These are safe to replay in
# order against the Bay County demo scenario.
ACTION_EXAMPLES: dict[str, dict[str, Any]] = {
    "increase_stock": {"warehouse": "W1", "quantity": 10},
    "decrease_stock": {"warehouse": "W1", "quantity": 10},
    "set_stock": {"warehouse": "W1", "quantity": 50},
    "warehouse_offline": {"warehouse": "W2"},
    "warehouse_online": {"warehouse": "W2"},
    "set_demand": {"shelter": "S3", "quantity": 120},
    "truck_offline": {"truck": "T1"},
    "truck_online": {"truck": "T1"},
    "close_route": {"warehouse": "W4", "shelter": "S3"},
    "open_route": {"warehouse": "W4", "shelter": "S3"},
}

_EXAMPLE_TEXT: dict[str, str] = {
    "increase_stock": "A relief convoy delivered 10 kits to Warehouse A.",
    "decrease_stock": "We lost 10 food kits to flooding at Warehouse A.",
    "set_stock": "Set Warehouse A stock to 50 kits.",
    "warehouse_offline": "Warehouse B lost power and stopped shipping.",
    "warehouse_online": "Warehouse B is operational again.",
    "set_demand": "Shelter 3 now needs 120 kits.",
    "truck_offline": "Our delivery truck T1 broke down.",
    "truck_online": "Delivery truck T1 is back in service.",
    "close_route": "The road between Warehouse D and Shelter 3 is blocked.",
    "open_route": "The road between Warehouse D and Shelter 3 is open again.",
}


def example_requests() -> dict[str, dict[str, Any]]:
    """Full apply-request bodies, one per action."""
    return {
        action: {
            "text": _EXAMPLE_TEXT[action],
            "operation": {"action": action, **fields},
        }
        for action, fields in ACTION_EXAMPLES.items()
    }


class ParseRequest(BaseModel):
    """Request body for the incident parse endpoint."""

    text: str = Field(
        ...,
        min_length=1,
        description="Natural-language operational message, e.g. "
        "'Warehouse A lost 40 food kits.' or "
        "'The road between Warehouse A and Shelter 2 is blocked.'",
    )


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.post("/incidents/parse", response_model=IncidentUpdate)
def parse_incident_route(request: ParseRequest) -> IncidentUpdate:
    """Parse a natural-language CrisisOps message into a structured operation.

    Handles all supported operations (stock changes, warehouse/truck status,
    shelter demand, route open/close). Returns HTTP 422 with a clear message
    when the input is ambiguous or cannot be parsed. Database updates are not
    performed here.
    """
    try:
        return parse_incident(request.text)
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# Backwards-compatible alias: the original endpoint path.
@app.post("/inventory/parse", response_model=IncidentUpdate, include_in_schema=False)
def parse_inventory(request: ParseRequest) -> IncidentUpdate:
    """Deprecated alias for POST /incidents/parse."""
    return parse_incident_route(request)


class ApplyRequest(BaseModel):
    """A confirmed operation plus the message stored on the incident row."""

    text: str = Field(..., min_length=1, description="Description recorded with the incident.")
    operation: IncidentUpdate
    method: Literal["optimal", "greedy", "compare"] | None = Field(
        default=None,
        description="Planner to run after the update. Omit to use ALLOCATOR_METHOD.",
    )


@app.get("/incidents/actions")
def list_actions() -> dict[str, dict[str, Any]]:
    """Example JSON body for every action the apply endpoint accepts."""
    return example_requests()


@app.post("/incidents/apply")
def apply_incident(request: ApplyRequest) -> dict[str, Any]:
    """Apply one confirmed operation, then recompute the delivery plan.

    Every action is applied and then replanned: stock changes, warehouse
    status, shelter demand, truck status, and route open/close. ``method``
    omitted uses ``ALLOCATOR_METHOD`` from the environment.

    Targets may be database ids (``W1``, ``S3``, ``T1``) or what
    ``/incidents/parse`` returns (``A``, ``3``); the response echoes the
    resolved ids. Returns HTTP 422 when the action or a target is invalid, or
    the change would make stock or demand negative.
    """
    operation = request.operation.model_dump(mode="json", exclude_none=True)
    conn = connect()
    try:
        operation = resolve_operation(conn, operation)
        apply_operation(conn, operation, request.text)
        plans = plan_from_db(conn, method=request.method)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=422,
            detail="Operation rejected because stock or demand would become negative.",
        ) from exc
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database is not initialized: {exc}",
        ) from exc
    finally:
        conn.close()
    return {"text": request.text, "operation": operation, "plans": plans}
