"""Shared fixtures. Uses fakeredis with the Lua engine so atomic scripts run."""

from __future__ import annotations

import secrets
from collections.abc import Iterator

import fakeredis
import pytest

from waiting_room.core.engine import WaitingRoom
from waiting_room.core.settings import (
    AdmissionPolicy,
    FailureMode,
    RedisConfig,
    WaitingRoomConfig,
)


@pytest.fixture
def redis_client() -> Iterator[fakeredis.FakeRedis]:
    client = fakeredis.FakeRedis()
    try:
        yield client
    finally:
        client.flushall()


@pytest.fixture
def secret() -> str:
    return secrets.token_urlsafe(48)


@pytest.fixture
def make_room(redis_client: fakeredis.FakeRedis, secret: str):
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
