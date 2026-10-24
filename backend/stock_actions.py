"""Handlers for incoming stock-action JSON.

Kept separate from ``backend/api.py`` so route changes do not collide with
this logic. Routes can import it either as a package module or as a sibling:

    from backend.stock_actions import handle_incoming_json
    from stock_actions import handle_incoming_json

Expected JSON object::

    {"action": "increase_stock", "value": 10}
    {"action": "decrease_stock", "value": 4}

``action`` is one of the two names above. ``value`` is the integer associated
with that action.
"""

from __future__ import annotations

from typing import Any, Mapping

INCREASE_STOCK = "increase_stock"
DECREASE_STOCK = "decrease_stock"

SUPPORTED_ACTIONS = frozenset({INCREASE_STOCK, DECREASE_STOCK})


class StockActionError(ValueError):
    """Raised when an incoming stock-action payload cannot be handled."""


def increase_stock(value: int) -> dict[str, Any]:
    """Handle an ``increase_stock`` action for ``value`` units."""
    amount = _require_amount(value)
    return {"action": INCREASE_STOCK, "value": amount}


def decrease_stock(value: int) -> dict[str, Any]:
    """Handle a ``decrease_stock`` action for ``value`` units."""
    amount = _require_amount(value)
    return {"action": DECREASE_STOCK, "value": amount}


def handle_incoming_json(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Dispatch an incoming JSON object to the matching stock action."""
    if not isinstance(payload, Mapping):
        raise StockActionError("Expected a JSON object.")

    action = payload.get("action")
    if action not in SUPPORTED_ACTIONS:
        allowed = ", ".join(sorted(SUPPORTED_ACTIONS))
        raise StockActionError(f"Unsupported action {action!r}. Expected one of: {allowed}.")

    if "value" not in payload:
        raise StockActionError("Missing integer field 'value'.")

    amount = _require_amount(payload["value"])
    if action == INCREASE_STOCK:
        return increase_stock(amount)
    return decrease_stock(amount)


def _require_amount(value: object) -> int:
    """Return ``value`` when it is an integer. Booleans are rejected."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise StockActionError("Field 'value' must be an integer.")
    return value
