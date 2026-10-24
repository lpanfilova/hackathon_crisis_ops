"""Tests for the incident parser.

These tests mock the Bedrock client, so they run offline, cost nothing, and
exercise our parsing/validation logic rather than the model itself. They cover
every CrisisOps operation the manager can trigger via natural language.
"""

import pytest

import ai.incident_parser as parser
from ai.incident_parser import (
    IncidentAction,
    IncidentUpdate,
    ParseError,
    parse_incident,
)


def _tool_response(**fields):
    """Build a fake Bedrock converse response that calls our tool."""
    return {
        "output": {
            "message": {
                "content": [
                    {
                        "toolUse": {
                            "name": "record_incident",
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


# --- The six example operations from the spec --------------------------------


def test_warehouse_offline(mock_bedrock):
    mock_bedrock(_tool_response(action="warehouse_offline", warehouse="B"))
    result = parse_incident("Warehouse B lost power and stopped shipping.")
    assert result.model_dump() == {
        "action": "warehouse_offline",
        "warehouse": "B",
        "shelter": None,
        "truck": None,
        "quantity": None,
    }


def test_decrease_stock(mock_bedrock):
    mock_bedrock(
        _tool_response(action="decrease_stock", warehouse="A", quantity=40)
    )
    result = parse_incident("We lost 40 food kits to flooding at Warehouse A.")
    assert result.action is IncidentAction.DECREASE_STOCK
    assert result.warehouse == "A"
    assert result.quantity == 40


def test_set_demand(mock_bedrock):
    mock_bedrock(_tool_response(action="set_demand", shelter="3", quantity=100))
    result = parse_incident("Shelter 3 now needs 100 kits instead of 40.")
    assert result.action is IncidentAction.SET_DEMAND
    assert result.shelter == "3"
    assert result.quantity == 100


def test_warehouse_online(mock_bedrock):
    mock_bedrock(_tool_response(action="warehouse_online", warehouse="B"))
    result = parse_incident("Warehouse B is operational again.")
    assert result.action is IncidentAction.WAREHOUSE_ONLINE
    assert result.warehouse == "B"


def test_truck_offline(mock_bedrock):
    mock_bedrock(_tool_response(action="truck_offline", truck="T1"))
    result = parse_incident("Our delivery truck T1 broke down.")
    assert result.action is IncidentAction.TRUCK_OFFLINE
    assert result.truck == "T1"


def test_close_route(mock_bedrock):
    mock_bedrock(
        _tool_response(action="close_route", warehouse="A", shelter="2")
    )
    result = parse_incident(
        "The road between Warehouse A and Shelter 2 is blocked."
    )
    assert result.action is IncidentAction.CLOSE_ROUTE
    assert result.warehouse == "A"
    assert result.shelter == "2"


# --- Normalization & inverse actions -----------------------------------------


def test_identifier_normalization(mock_bedrock):
    """'Warehouse A' / 'Shelter 3' collapse to just the token."""
    mock_bedrock(
        _tool_response(
            action="close_route", warehouse="Warehouse A", shelter="Shelter 2"
        )
    )
    result = parse_incident("Road from Warehouse A to Shelter 2 blocked.")
    assert result.warehouse == "A"
    assert result.shelter == "2"


def test_truck_id_uppercased(mock_bedrock):
    mock_bedrock(_tool_response(action="truck_online", truck="t2"))
    result = parse_incident("Truck t2 is back in service.")
    assert result.truck == "T2"


def test_open_route(mock_bedrock):
    mock_bedrock(
        _tool_response(action="open_route", warehouse="A", shelter="2")
    )
    result = parse_incident("The road to Shelter 2 from Warehouse A is clear.")
    assert result.action is IncidentAction.OPEN_ROUTE


def test_increase_stock(mock_bedrock):
    mock_bedrock(
        _tool_response(action="increase_stock", warehouse="B", quantity=10)
    )
    result = parse_incident("Warehouse B received 10 more kits.")
    assert result.action is IncidentAction.INCREASE_STOCK
    assert result.quantity == 10


def test_set_stock(mock_bedrock):
    mock_bedrock(
        _tool_response(action="set_stock", warehouse="A", quantity=75)
    )
    result = parse_incident("Set Warehouse A stock to 75 kits.")
    assert result.action is IncidentAction.SET_STOCK
    assert result.quantity == 75


# --- Error / validation paths ------------------------------------------------


def test_empty_input_raises():
    with pytest.raises(ParseError):
        parse_incident("   ")


def test_model_decline_raises(mock_bedrock):
    """A CANNOT_PARSE text reply surfaces as a ParseError with the reason."""
    mock_bedrock(_text_response("CANNOT_PARSE: no quantity provided"))
    with pytest.raises(ParseError) as exc:
        parse_incident("hello there")
    assert "no quantity provided" in str(exc.value)


def test_invalid_action_raises(mock_bedrock):
    """Out-of-schema action fails Pydantic validation -> ParseError."""
    mock_bedrock(_tool_response(action="teleport_stock", warehouse="A"))
    with pytest.raises(ParseError):
        parse_incident("do something weird")


def test_missing_required_field_raises(mock_bedrock):
    """decrease_stock without quantity is rejected by the model validator."""
    mock_bedrock(_tool_response(action="decrease_stock", warehouse="A"))
    with pytest.raises(ParseError):
        parse_incident("Warehouse A lost some kits.")


def test_close_route_requires_both_targets(mock_bedrock):
    """close_route needs both a warehouse and a shelter."""
    mock_bedrock(_tool_response(action="close_route", warehouse="A"))
    with pytest.raises(ParseError):
        parse_incident("A road near Warehouse A is blocked.")


def test_negative_quantity_rejected():
    with pytest.raises(Exception):
        IncidentUpdate(action="set_stock", warehouse="A", quantity=-1)


# --- Backward-compatible aliases still importable ----------------------------


def test_legacy_aliases_exist():
    from ai.incident_parser import (  # noqa: F401
        InventoryAction,
        InventoryUpdate,
        parse_inventory_update,
    )

    assert InventoryUpdate is IncidentUpdate
    assert InventoryAction is IncidentAction
