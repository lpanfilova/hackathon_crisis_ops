"""Natural-language warehouse inventory parsing via Amazon Bedrock (Claude Haiku).

This module turns a free-text inventory update such as

    "Warehouse A lost 40 food kits."

into a validated, structured object:

    {"action": "decrease_stock", "warehouse": "A",
     "item": "food_kits", "quantity": 40}

It uses the Bedrock `converse` API with a forced tool call so the model is
constrained to emit arguments matching our schema, which is far more reliable
than parsing free-form JSON out of the text.

Database updates are intentionally NOT performed here.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Any

import boto3
from pydantic import BaseModel, Field, ValidationError, field_validator

from backend.config import get_settings


class ParseError(Exception):
    """Raised when the model cannot confidently parse the input.

    Carries a human-readable message suitable for returning to the caller.
    """


class InventoryAction(str, Enum):
    """Supported inventory mutations."""

    INCREASE_STOCK = "increase_stock"
    DECREASE_STOCK = "decrease_stock"
    SET_STOCK = "set_stock"


class InventoryUpdate(BaseModel):
    """Validated structured representation of an inventory update."""

    action: InventoryAction
    warehouse: str = Field(..., min_length=1)
    item: str = Field(..., min_length=1)
    quantity: int = Field(..., ge=0)

    @field_validator("item")
    @classmethod
    def _normalize_item(cls, value: str) -> str:
        """Normalize the item name to lowercase snake_case."""
        return value.strip().lower().replace(" ", "_").replace("-", "_")

    @field_validator("warehouse")
    @classmethod
    def _strip_warehouse(cls, value: str) -> str:
        return value.strip()


# --- Bedrock tool schema -----------------------------------------------------

# The tool is how we force structured output. The model must "call" this tool
# with arguments matching the input schema, giving us typed fields back instead
# of hoping it returns clean JSON in prose.
_TOOL_NAME = "record_inventory_update"

_TOOL_SPEC: dict[str, Any] = {
    "toolSpec": {
        "name": _TOOL_NAME,
        "description": (
            "Record a structured warehouse inventory update extracted from a "
            "natural-language message. Call this exactly once with the fields "
            "you extracted."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [a.value for a in InventoryAction],
                        "description": (
                            "increase_stock when stock is added/received/gained; "
                            "decrease_stock when stock is lost/used/removed/"
                            "damaged; set_stock when an absolute count is stated."
                        ),
                    },
                    "warehouse": {
                        "type": "string",
                        "description": "Warehouse identifier, e.g. 'A'. "
                        "Strip the word 'warehouse'.",
                    },
                    "item": {
                        "type": "string",
                        "description": "Item name in lowercase snake_case, "
                        "e.g. 'food_kits'.",
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "Non-negative integer quantity involved.",
                    },
                },
                "required": ["action", "warehouse", "item", "quantity"],
            }
        },
    }
}

_SYSTEM_PROMPT = (
    "You extract structured warehouse inventory updates from short "
    "natural-language messages. Always respond by calling the "
    f"`{_TOOL_NAME}` tool with the extracted fields. If the message does not "
    "describe a concrete inventory change (missing warehouse, item, quantity, "
    "or intent), do NOT guess: instead reply in plain text beginning with "
    "'CANNOT_PARSE:' followed by a short reason."
)


@lru_cache
def _get_bedrock_client():
    """Return a cached Bedrock Runtime client."""
    settings = get_settings()
    return boto3.client("bedrock-runtime", region_name=settings.aws_region)


def parse_inventory_update(text: str) -> InventoryUpdate:
    """Parse a natural-language inventory update into a structured object.

    Args:
        text: Free-text message, e.g. "Warehouse A lost 40 food kits."

    Returns:
        A validated :class:`InventoryUpdate`.

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
        raise ParseError(reason or "Could not parse a valid inventory update.")

    try:
        return InventoryUpdate.model_validate(tool_input)
    except ValidationError as exc:
        raise ParseError(f"Model output failed validation: {exc}") from exc


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
