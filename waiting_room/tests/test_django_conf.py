"""Reading the WAITING_ROOM setting."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from waiting_room.adapters.django.conf import load_configs
from waiting_room.core.settings import _PolicyKind

_SECRET = "x" * 40


def _rooms(**room: object) -> dict[str, object]:
    return {"ROOMS": {"r": {"SECRET_KEY": _SECRET, "TARGET_URL": "/t/", **room}}}


def test_capacity_aware_falls_back_to_room_capacity() -> None:
    with override_settings(WAITING_ROOM=_rooms(POLICY={"KIND": "capacity_aware"}, CAPACITY=7)):
        cfg = load_configs()["r"]
    assert cfg.policy.kind is _PolicyKind.CAPACITY_AWARE
    assert cfg.policy.capacity == 7


def test_capacity_aware_policy_capacity_wins() -> None:
    policy = {"KIND": "capacity_aware", "CAPACITY": 3}
    with override_settings(WAITING_ROOM=_rooms(POLICY=policy, CAPACITY=7)):
        assert load_configs()["r"].policy.capacity == 3


def test_invalid_policy_is_improperly_configured() -> None:
    policy = {"KIND": "time_bucket", "ADMIT_PER_SECOND": 0}
    with (
        override_settings(WAITING_ROOM=_rooms(POLICY=policy)),
        pytest.raises(ImproperlyConfigured, match="POLICY"),
    ):
        load_configs()
