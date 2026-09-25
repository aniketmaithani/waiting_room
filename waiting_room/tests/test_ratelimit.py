"""Enqueue rate limiting."""

from __future__ import annotations

import pytest
import redis

from waiting_room.core.exceptions import RateLimitedError
from waiting_room.core.ratelimit import RedisRateLimiter
from waiting_room.core.settings import FailureMode


def test_limit_is_enforced(redis_client: redis.Redis) -> None:
    limiter = RedisRateLimiter(redis_client, limit=2, window_seconds=60, key_prefix="t:rl")
    assert [limiter.acquire("k") for _ in range(3)] == [True, True, False]


def test_window_is_not_extended_by_further_hits(redis_client: redis.Redis) -> None:
    limiter = RedisRateLimiter(redis_client, limit=100, window_seconds=60, key_prefix="t:rl")
    limiter.acquire("k")
    redis_client.expire("t:rl:k", 5)
    limiter.acquire("k")
    assert redis_client.ttl("t:rl:k") <= 5


def test_engine_raises_rate_limited(make_room) -> None:
    room = make_room(rate_limit_per_minute=1)
    room.enqueue(ip="1.1.1.1", user_agent="ua")
    with pytest.raises(RateLimitedError):
        room.enqueue(ip="1.1.1.1", user_agent="ua")


def test_rate_limit_is_not_bypassed_in_fail_open(make_room) -> None:
    room = make_room(rate_limit_per_minute=1, failure_mode=FailureMode.FAIL_OPEN)
    room.enqueue(ip="1.1.1.1", user_agent="ua")
    with pytest.raises(RateLimitedError):
        room.enqueue(ip="1.1.1.1", user_agent="ua")


def test_rate_limits_are_scoped_per_room(make_room) -> None:
    make_room(name="a", rate_limit_per_minute=1).enqueue(ip="1.1.1.1", user_agent="ua")
    make_room(name="b", rate_limit_per_minute=1).enqueue(ip="1.1.1.1", user_agent="ua")
