"""Natural-language CrisisOps incident parsing via Amazon Bedrock (Claude Haiku).

This module turns a free-text operational message from the distribution
manager into a validated, structured object describing a single database
operation. It covers all CrisisOps operations against the schema in
`backend/schema.sql`:

    * warehouse goes offline / back online   (warehouses.is_operational)
    * warehouse stock increase / decrease / set   (warehouses.usable_stock)
    * shelter demand set                      (shelters.demand)
    * truck goes offline / back online        (trucks.is_operational)
    * delivery route closed / opened          (delivery_routes.is_open)

Examples
--------
    "Warehouse A lost 40 food kits."
        -> {"action": "decrease_stock", "warehouse": "A", "quantity": 40}
    "Warehouse B lost power and stopped shipping."
        -> {"action": "warehouse_offline", "warehouse": "B"}
    "Shelter 3 now needs 100 kits instead of 40."
        -> {"action": "set_demand", "shelter": "3", "quantity": 100}
    "Our delivery truck T1 broke down."
        -> {"action": "truck_offline", "truck": "T1"}
    "The road between Warehouse A and Shelter 2 is blocked."
        -> {"action": "close_route", "warehouse": "A", "shelter": "2"}

It uses the Bedrock `converse` API with a forced tool call so the model is
constrained to emit arguments matching our schema, which is far more reliable
than parsing free-form JSON out of prose.

Database updates are intentionally NOT performed here; this module only
produces the structured intent. The functions in `init_db.py` apply it.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Any

import boto3
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from backend.config import get_settings


class ParseError(Exception):
    """Raised when the model cannot confidently parse the input.

    Carries a human-readable message suitable for returning to the caller.
    """


class IncidentAction(str, Enum):
    """Every operation the manager can trigger via natural language.

    Each value maps directly onto an operation function in ``init_db.py``.
    """

    # warehouse inventory
    INCREASE_STOCK = "increase_stock"
    DECREASE_STOCK = "decrease_stock"
    SET_STOCK = "set_stock"
    # warehouse operational status
    WAREHOUSE_OFFLINE = "warehouse_offline"
    WAREHOUSE_ONLINE = "warehouse_online"
    # shelter demand
    SET_DEMAND = "set_demand"
    # truck operational status
    TRUCK_OFFLINE = "truck_offline"
    TRUCK_ONLINE = "truck_online"
    # delivery route status
    CLOSE_ROUTE = "close_route"
    OPEN_ROUTE = "open_route"


# Which target/quantity fields each action requires.
_ACTIONS_NEEDING_WAREHOUSE = {
    IncidentAction.INCREASE_STOCK,
    IncidentAction.DECREASE_STOCK,
    IncidentAction.SET_STOCK,
    IncidentAction.WAREHOUSE_OFFLINE,
    IncidentAction.WAREHOUSE_ONLINE,
    IncidentAction.CLOSE_ROUTE,
    IncidentAction.OPEN_ROUTE,
}
_ACTIONS_NEEDING_SHELTER = {
    IncidentAction.SET_DEMAND,
    IncidentAction.CLOSE_ROUTE,
    IncidentAction.OPEN_ROUTE,
}
_ACTIONS_NEEDING_TRUCK = {
    IncidentAction.TRUCK_OFFLINE,
    IncidentAction.TRUCK_ONLINE,
}
_ACTIONS_NEEDING_QUANTITY = {
    IncidentAction.INCREASE_STOCK,
    IncidentAction.DECREASE_STOCK,
    IncidentAction.SET_STOCK,
    IncidentAction.SET_DEMAND,
}


class IncidentUpdate(BaseModel):
    """Validated structured representation of a single CrisisOps operation.

    Only the fields relevant to ``action`` are populated; the rest stay None.
    A model-level validator enforces that the required targets are present for
    the given action, so downstream code can trust the shape.
    """

    action: IncidentAction
    warehouse: str | None = Field(default=None)
    shelter: str | None = Field(default=None)
    truck: str | None = Field(default=None)
    quantity: int | None = Field(default=None, ge=0)

    @field_validator("warehouse", "shelter")
    @classmethod
    def _normalize_location_id(cls, value: str | None) -> str | None:
        """Strip the word 'warehouse'/'shelter' and surrounding whitespace.

        The DB uses ids like 'W1'/'S1' but managers say "Warehouse A" or
        "Shelter 3". We keep just the identifier token (e.g. 'A', '3'); mapping
        to the canonical row id happens in the DB layer.
        """
        if value is None:
            return None
        cleaned = value.strip()
        lowered = cleaned.lower()
        for prefix in ("warehouse", "shelter"):
            if lowered.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
                break
        return cleaned

    @field_validator("truck")
    @classmethod
    def _normalize_truck_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().upper()

    @model_validator(mode="after")
    def _require_fields_for_action(self) -> "IncidentUpdate":
        """Ensure the fields an action needs are present (and others aren't noise)."""
        missing: list[str] = []
        if self.action in _ACTIONS_NEEDING_WAREHOUSE and not self.warehouse:
            missing.append("warehouse")
        if self.action in _ACTIONS_NEEDING_SHELTER and not self.shelter:
            missing.append("shelter")
        if self.action in _ACTIONS_NEEDING_TRUCK and not self.truck:
            missing.append("truck")
        if self.action in _ACTIONS_NEEDING_QUANTITY and self.quantity is None:
            missing.append("quantity")
        if missing:
            raise ValueError(
                f"action '{self.action.value}' requires: {', '.join(missing)}"
            )
        return self


# --- Backward-compatible aliases --------------------------------------------
# The original API/tests referred to InventoryUpdate / InventoryAction /
# parse_inventory_update. Keep those names working.
InventoryAction = IncidentAction
InventoryUpdate = IncidentUpdate


# --- Bedrock tool schema -----------------------------------------------------

# The tool is how we force structured output. The model must "call" this tool
# with arguments matching the input schema, giving us typed fields back instead
# of hoping it returns clean JSON in prose.
_TOOL_NAME = "record_incident"

_TOOL_SPEC: dict[str, Any] = {
    "toolSpec": {
        "name": _TOOL_NAME,
        "description": (
            "Record a single structured CrisisOps operation extracted from a "
            "natural-language message from the distribution manager. Call this "
            "exactly once with the fields relevant to the action."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [a.value for a in IncidentAction],
                        "description": (
                            "The operation to perform:\n"
                            "- increase_stock: warehouse gained/received kits\n"
                            "- decrease_stock: warehouse lost/used/damaged kits "
                            "(flooding, spoilage, etc.)\n"
                            "- set_stock: warehouse stock set to an absolute number\n"
                            "- warehouse_offline: warehouse stopped operating "
                            "(lost power, shut down, can't ship)\n"
                            "- warehouse_online: warehouse operating again\n"
                            "- set_demand: a shelter's required kits set to an "
                            "absolute number\n"
                            "- truck_offline: a truck is unavailable "
                            "(broke down, out of service)\n"
                            "- truck_online: a truck is back in service\n"
                            "- close_route: the road/route between a warehouse "
                            "and a shelter is blocked/closed\n"
                            "- open_route: that route is passable again"
                        ),
                    },
                    "warehouse": {
                        "type": "string",
                        "description": "Warehouse identifier only, e.g. 'A' or "
                        "'W1'. Strip the word 'warehouse'. Required for stock, "
                        "warehouse status, and route actions.",
                    },
                    "shelter": {
                        "type": "string",
                        "description": "Shelter identifier only, e.g. '3' or "
                        "'S3'. Strip the word 'shelter'. Required for set_demand "
                        "and route actions.",
                    },
                    "truck": {
                        "type": "string",
                        "description": "Truck identifier, e.g. 'T1'. Required "
                        "for truck_offline / truck_online.",
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "Non-negative integer. For "
                        "increase/decrease_stock it is the amount changed; for "
                        "set_stock/set_demand it is the new absolute value.",
                    },
                },
                "required": ["action"],
            }
        },
    }
}

_SYSTEM_PROMPT = (
    "You extract a single structured CrisisOps operation from a short "
    "natural-language message sent by a disaster-response distribution "
    f"manager. Always respond by calling the `{_TOOL_NAME}` tool with the "
    "action and only the fields that action needs. Identifiers: give just the "
    "token (e.g. 'A', '3', 'T1'), not the words 'warehouse'/'shelter'. "
    "'lost N kits', flooding, spoilage, or damage mean decrease_stock. "
    "'lost power', 'stopped shipping', or 'shut down' mean warehouse_offline. "
    "'operational again' / 'back online' mean warehouse_online. A truck that "
    "'broke down' means truck_offline. A 'blocked' or 'closed' road between a "
    "warehouse and a shelter means close_route. If the message does not "
    "describe one concrete operation (missing a required target or quantity, "
    "or ambiguous intent), do NOT guess: reply in plain text beginning with "
    "'CANNOT_PARSE:' followed by a short reason."
)


@lru_cache
def _get_bedrock_client():
    """Return a cached Bedrock Runtime client."""
    settings = get_settings()
    return boto3.client("bedrock-runtime", region_name=settings.aws_region)


def parse_incident(text: str) -> IncidentUpdate:
    """Parse a natural-language CrisisOps message into a structured operation.

    Args:
        text: Free-text message, e.g. "Warehouse A lost 40 food kits." or
            "The road between Warehouse A and Shelter 2 is blocked."

    Returns:
        A validated :class:`IncidentUpdate`.

    Raises:
        ParseError: If the input is empty, the model declines to parse it, or
            the model's output fails schema validation.
    """
    if not text or not text.strip():
        raise ParseError("Input text is empty.")

    settings = get_settings()
    client = _get_bedrock_client()

    try:
        response = client.converse(
            modelId=settings.bedrock_model_id,
            system=[{"text": _SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": text}]}],
            toolConfig={
                "tools": [_TOOL_SPEC],
                # Nudge the model toward using the tool.
                "toolChoice": {"auto": {}},
            },
            inferenceConfig={
                "maxTokens": settings.bedrock_max_tokens,
                "temperature": settings.bedrock_temperature,
            },
        )
    except Exception as exc:  # noqa: BLE001 - surface any Bedrock/client error
        raise ParseError(f"Bedrock request failed: {exc}") from exc

    tool_input = _extract_tool_input(response)
    if tool_input is None:
        reason = _extract_decline_reason(response)
        raise ParseError(reason or "Could not parse a valid operation.")

    try:
        return IncidentUpdate.model_validate(tool_input)
    except ValidationError as exc:
        raise ParseError(f"Model output failed validation: {exc}") from exc


# Backward-compatible alias for the original function name.
parse_inventory_update = parse_incident


def _extract_tool_input(response: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the tool-use arguments out of a Bedrock converse response."""
    message = response.get("output", {}).get("message", {})
    for block in message.get("content", []):
        tool_use = block.get("toolUse")
        if tool_use and tool_use.get("name") == _TOOL_NAME:
            return tool_use.get("input")
    return None


def _extract_decline_reason(response: dict[str, Any]) -> str | None:
    """Extract a plain-text decline reason ('CANNOT_PARSE: ...') if present."""
    message = response.get("output", {}).get("message", {})
    texts = [
        block["text"]
        for block in message.get("content", [])
        if "text" in block
    ]
    joined = " ".join(texts).strip()
    if not joined:
        return None
    marker = "CANNOT_PARSE:"
    if marker in joined:
        return joined.split(marker, 1)[1].strip() or "Input could not be parsed."
    return joined
