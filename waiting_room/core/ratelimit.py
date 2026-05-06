"""Per-key rate limiter.

The Redis implementation uses a fixed-window counter (INCR + EXPIRE) — cheap
and correct under high concurrency. Burst tolerance comes from the window
length: shorter windows = tighter caps but more counter churn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from waiting_room.core.exceptions import BackendUnavailableError
from waiting_room.core.interfaces import RateLimiter

if TYPE_CHECKING:
    import redis as redis_pkg


class NoopRateLimiter(RateLimiter):
    """Always allows. Useful in tests and dev mode."""

    def acquire(self, key: str) -> bool:
        del key
        return True


class RedisRateLimiter(RateLimiter):
    """Fixed-window counter (``INCR`` + ``EXPIRE``)."""

    def __init__(
        self,
        client: redis_pkg.Redis,
        *,
        limit: int,
        window_seconds: int = 60,
        key_prefix: str = "wr:rl",
    ) -> None:
        if limit <= 0:
            msg = "limit must be > 0"
            raise ValueError(msg)
        self._client = client
        self._limit = int(limit)
        self._window = int(window_seconds)
        self._prefix = key_prefix.rstrip(":")

    def acquire(self, key: str) -> bool:
        try:
            pipe = self._client.pipeline()
            full_key = f"{self._prefix}:{key}"
            pipe.incr(full_key)
            pipe.expire(full_key, self._window)
            count, _ = pipe.execute()
        except _redis_errors() as exc:
            raise BackendUnavailableError(str(exc)) from exc
        return int(count) <= self._limit


def _redis_errors() -> tuple[type[BaseException], ...]:
    try:
        import redis as redis_pkg
    except ImportError:
        return (OSError,)
    return (redis_pkg.RedisError, OSError)


__all__ = ["NoopRateLimiter", "RedisRateLimiter"]
