"""Backend API routes."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ai.incident_parser import InventoryUpdate, ParseError, parse_inventory_update

app = FastAPI(title="Crisis Ops Inventory API")


class ParseRequest(BaseModel):
    """Request body for the inventory parse endpoint."""

    text: str = Field(
        ...,
        min_length=1,
        description="Natural-language inventory update, "
        "e.g. 'Warehouse A lost 40 food kits.'",
    )


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.post("/inventory/parse", response_model=InventoryUpdate)
def parse_inventory(request: ParseRequest) -> InventoryUpdate:
    """Parse a natural-language inventory update into structured JSON.

    Returns HTTP 422 with a clear message when the input is ambiguous or
    cannot be parsed into a valid inventory update. Database updates are not
    performed.
    """
    try:
        return parse_inventory_update(request.text)
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
