"""Tests for allocator settings in backend/config.py."""

import pytest
from pydantic import ValidationError

from backend.config import AllocatorSettings
from optimization.allocator import PlanningParams

_KEYS = ["ALLOCATOR_METHOD", "PLANNING_HORIZON_HOURS", "AVG_SPEED_KMH",
         "HANDLING_HOURS_PER_TRIP", "OPTIMIZER_TIME_LIMIT_S", "OPTIMIZER_ROUNDING_TIME_S",
         "AWS_REGION", "BEDROCK_MODEL_ID"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Isolate from the shell and from the local .env file."""
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)


def test_defaults_need_no_env_file_or_aws():
    s = AllocatorSettings(_env_file=None)
    assert s.allocator_method == "optimal"
    assert PlanningParams.from_settings(s) == PlanningParams()


def test_env_vars_override_defaults(monkeypatch):
    monkeypatch.setenv("ALLOCATOR_METHOD", "compare")
    monkeypatch.setenv("PLANNING_HORIZON_HOURS", "4")
    s = AllocatorSettings(_env_file=None)
    assert s.allocator_method == "compare"
    assert PlanningParams.from_settings(s).horizon_hours == 4.0


@pytest.mark.parametrize("key,value", [
    ("ALLOCATOR_METHOD", "fastest"),
    ("PLANNING_HORIZON_HOURS", "0"),
    ("AVG_SPEED_KMH", "-10"),
    ("HANDLING_HOURS_PER_TRIP", "-1"),
])
def test_invalid_values_fail_loudly(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        AllocatorSettings(_env_file=None)
