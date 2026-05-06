"""Management commands."""

from __future__ import annotations

from io import StringIO

import fakeredis
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from waiting_room.adapters.django import registry
from waiting_room.core.engine import WaitingRoom
from waiting_room.core.settings import (
    AdmissionPolicy,
    RedisConfig,
    WaitingRoomConfig,
)


@pytest.fixture
def cmd_room() -> WaitingRoom:
    cfg = WaitingRoomConfig(
        name="cmd",
        secret_key="x" * 64,
        target_url="/x/",
        capacity=4,
        policy=AdmissionPolicy.time_bucket(admit_per_second=10, burst=10),
        storage=RedisConfig(url="redis://fake/0"),
        rate_limit_per_ip_per_minute=1_000_000,
    )
    room = WaitingRoom(cfg, redis_client=fakeredis.FakeRedis())
    registry.reset()
    registry.register("cmd", room)
    yield room
    registry.reset()


def test_status_command_lists_room(cmd_room: WaitingRoom) -> None:
    cmd_room.enqueue(ip="1", user_agent="ua")
    buf = StringIO()
    call_command("waiting_room_status", stdout=buf)
    out = buf.getvalue()
    assert "cmd" in out
    assert "QUEUE" in out


def test_kill_switch_command_toggles_state(cmd_room: WaitingRoom) -> None:
    buf = StringIO()
    call_command("waiting_room_kill_switch", "cmd", "on", stdout=buf)
    assert cmd_room.is_kill_switch_engaged() is True
    call_command("waiting_room_kill_switch", "cmd", "off", stdout=buf)
    assert cmd_room.is_kill_switch_engaged() is False


def test_kill_switch_unknown_room_errors() -> None:
    with pytest.raises(CommandError):
        call_command("waiting_room_kill_switch", "nope", "on")


def test_reclaim_command_runs_for_all(cmd_room: WaitingRoom) -> None:
    cmd_room.enqueue(ip="1", user_agent="ua")
    buf = StringIO()
    call_command("waiting_room_reclaim", stdout=buf)
    assert "cmd: reclaimed" in buf.getvalue()


def test_flush_requires_yes(cmd_room: WaitingRoom) -> None:
    with pytest.raises(CommandError):
        call_command("waiting_room_flush", "cmd")


def test_flush_drops_keys(cmd_room: WaitingRoom) -> None:
    cmd_room.enqueue(ip="1", user_agent="ua")
    assert cmd_room._safe_queue_size() == 1
    buf = StringIO()
    call_command("waiting_room_flush", "cmd", "--yes", stdout=buf)
    assert cmd_room._safe_queue_size() == 0
