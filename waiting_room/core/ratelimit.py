"""Per-key rate limiter.

The Redis implementation uses a fixed-window counter — cheap and correct under
high concurrency. The window starts at the first hit and is never extended, so
a key is always released ``window_seconds`` after its window opened. Burst
tolerance comes from the window length: shorter windows mean tighter caps but
more counter churn.
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


_FIXED_WINDOW_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return count
"""


class RedisRateLimiter(RateLimiter):
    """Fixed-window counter (``INCR``, with ``EXPIRE`` set once per window)."""

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
        self._script = client.register_script(_FIXED_WINDOW_LUA)

    def acquire(self, key: str) -> bool:
        try:
            count = self._script(keys=[f"{self._prefix}:{key}"], args=[self._window])
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
