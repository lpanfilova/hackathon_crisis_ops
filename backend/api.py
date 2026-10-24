"""Backend API routes."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ai.incident_parser import IncidentUpdate, ParseError, parse_incident

app = FastAPI(title="CrisisOps Incident API")


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
