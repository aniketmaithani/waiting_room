"""Shared fixtures. Uses fakeredis with the Lua engine so atomic scripts run.

Set ``WAITING_ROOM_TEST_REDIS_URL`` (e.g. ``redis://localhost:6399/15``) to run
the suite against a real Redis instead. That database is flushed around every
test, so never point it at one holding real data.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator

import fakeredis
import pytest
import redis

from waiting_room.core.engine import WaitingRoom
from waiting_room.core.settings import (
    AdmissionPolicy,
    FailureMode,
    RedisConfig,
    WaitingRoomConfig,
)


@pytest.fixture
def redis_client() -> Iterator[redis.Redis]:
    url = os.environ.get("WAITING_ROOM_TEST_REDIS_URL")
    client: redis.Redis = redis.Redis.from_url(url) if url else fakeredis.FakeRedis()
    client.flushdb()
    try:
        yield client
    finally:
        client.flushdb()


@pytest.fixture
def secret() -> str:
    return secrets.token_urlsafe(48)


@pytest.fixture
def make_room(redis_client: redis.Redis, secret: str):
    """Factory that builds a WaitingRoom against the shared fakeredis client."""

    def _build(
        *,
        name: str = "test",
        admit_per_second: float = 5.0,
        capacity: int = 100,
        failure_mode: FailureMode = FailureMode.FAIL_CLOSED,
        rate_limit_per_minute: int = 1_000_000,
    ) -> WaitingRoom:
        cfg = WaitingRoomConfig(
            name=name,
            secret_key=secret,
            target_url="/checkout",
            capacity=capacity,
            policy=AdmissionPolicy.time_bucket(admit_per_second=admit_per_second, burst=capacity),
            storage=RedisConfig(url="redis://fake/0"),
            failure_mode=failure_mode,
            rate_limit_per_ip_per_minute=rate_limit_per_minute,
        )
        return WaitingRoom(cfg, redis_client=redis_client)

    return _build


@pytest.fixture
def wired_room(redis_client: redis.Redis) -> Iterator[WaitingRoom]:
    """Build a room and inject it into the registry, replacing what startup created."""
    from waiting_room.adapters.django import registry
    from waiting_room.adapters.django.signals import make_handler
    from waiting_room.core.engine import WaitingRoom
    from waiting_room.core.events import InProcessEventEmitter
    from waiting_room.core.settings import (
        AdmissionPolicy,
        RedisConfig,
        WaitingRoomConfig,
    )

    cfg = WaitingRoomConfig(
        name="default",
        secret_key="x" * 64,
        target_url="/checkout/",
        capacity=10,
        policy=AdmissionPolicy.time_bucket(admit_per_second=100, burst=100),
        storage=RedisConfig(url="redis://fake/0"),
        rate_limit_per_ip_per_minute=1_000_000,
        cookie_secure=False,
    )
    room = WaitingRoom(cfg, redis_client=redis_client)
    if isinstance(room.emitter, InProcessEventEmitter):
        room.emitter.subscribe(make_handler("default"))
    registry.reset()
    registry.register("default", room)
    yield room
    registry.reset()
