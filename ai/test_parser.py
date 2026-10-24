"""Tests for the incident parser.

These tests mock the Bedrock client, so they run offline, cost nothing, and
exercise our parsing/validation logic rather than the model itself.
"""

import pytest

import ai.incident_parser as parser
from ai.incident_parser import (
    InventoryUpdate,
    ParseError,
    parse_inventory_update,
)


def _tool_response(**fields):
    """Build a fake Bedrock converse response that calls our tool."""
    return {
        "output": {
            "message": {
                "content": [
                    {
                        "toolUse": {
                            "name": "record_inventory_update",
                            "input": fields,
                        }
                    }
                ]
            }
        }
    }


def _text_response(text):
    """Build a fake Bedrock converse response that returns plain text."""
    return {"output": {"message": {"content": [{"text": text}]}}}


class _FakeClient:
    def __init__(self, response):
        self._response = response

    def converse(self, **kwargs):
        return self._response


@pytest.fixture
def mock_bedrock(monkeypatch):
    """Patch the Bedrock client factory to return a canned response."""

    def _install(response):
        monkeypatch.setattr(
            parser, "_get_bedrock_client", lambda: _FakeClient(response)
        )

    return _install


def test_decrease_stock_example(mock_bedrock):
    """The canonical example from the spec returns the expected structure."""
    mock_bedrock(
        _tool_response(
            action="decrease_stock",
            warehouse="A",
            item="food kits",
            quantity=40,
        )
    )
    result = parse_inventory_update("Warehouse A lost 40 food kits.")
    assert result.model_dump() == {
        "action": "decrease_stock",
        "warehouse": "A",
        "item": "food_kits",  # normalized to snake_case
        "quantity": 40,
    }


def test_increase_stock(mock_bedrock):
    mock_bedrock(
        _tool_response(
            action="increase_stock", warehouse="B", item="water", quantity=10
        )
    )
    result = parse_inventory_update("Warehouse B received 10 water units.")
    assert result.action.value == "increase_stock"
    assert result.quantity == 10


def test_item_normalization(mock_bedrock):
    """Spaces and hyphens in item names collapse to snake_case."""
    mock_bedrock(
        _tool_response(
            action="set_stock",
            warehouse="C",
            item="First-Aid Kits",
            quantity=5,
        )
    )
    result = parse_inventory_update("Set warehouse C first-aid kits to 5.")
    assert result.item == "first_aid_kits"


def test_empty_input_raises():
    with pytest.raises(ParseError):
        parse_inventory_update("   ")


def test_model_decline_raises(mock_bedrock):
    """A CANNOT_PARSE text reply surfaces as a ParseError with the reason."""
    mock_bedrock(_text_response("CANNOT_PARSE: no quantity provided"))
    with pytest.raises(ParseError) as exc:
        parse_inventory_update("hello there")
    assert "no quantity provided" in str(exc.value)


def test_invalid_schema_raises(mock_bedrock):
    """Out-of-schema model output fails Pydantic validation -> ParseError."""
    mock_bedrock(
        _tool_response(
            action="teleport_stock",  # not a valid action
            warehouse="A",
            item="x",
            quantity=1,
        )
    )
    with pytest.raises(ParseError):
        parse_inventory_update("do something weird")


def test_negative_quantity_rejected():
    with pytest.raises(Exception):
        InventoryUpdate(
            action="set_stock", warehouse="A", item="x", quantity=-1
        )
