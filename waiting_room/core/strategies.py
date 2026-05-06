"""Built-in admission strategies.

The engine ticks the strategy and asks: ``slots_available(...)``. The strategy
returns the number of sessions to pop from the queue this tick.
"""

from __future__ import annotations

import threading
import time

from waiting_room.core.interfaces import AdmissionStrategy


class TimeBucketAdmission(AdmissionStrategy):
    """Token-bucket-style time-based admission.

    Admits up to ``admit_per_second`` sessions per second on average, with an
    optional ``burst`` allowance. Thread-safe — the same instance can serve
    many concurrent ticks across worker threads/processes (within one process).
    Across processes, capacity divergence is bounded by the storage layer's
    atomic ``admit_batch``.
    """

    def __init__(self, admit_per_second: float, *, burst: int = 0) -> None:
        if admit_per_second <= 0:
            msg = "admit_per_second must be > 0"
            raise ValueError(msg)
        self._rate = float(admit_per_second)
        self._capacity = float(max(burst, admit_per_second))
        self._tokens = self._capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def slots_available(self, *, queue_size: int, admitted: int, capacity: int) -> int:
        del admitted, capacity  # unused for purely time-based admission
        if queue_size <= 0:
            return 0
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._last = now
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            available = int(self._tokens)
            available = min(available, queue_size)
            self._tokens -= available
            return available


class CapacityAwareAdmission(AdmissionStrategy):
    """Admit only up to the free downstream capacity.

    Useful when the protected resource has a hard concurrency cap (e.g. payment
    gateway, ticket inventory). Combine with ``TimeBucketAdmission`` via
    ``CompositeAdmission`` if you also want a steady drip.
    """

    def slots_available(self, *, queue_size: int, admitted: int, capacity: int) -> int:
        if queue_size <= 0 or capacity <= 0:
            return 0
        return max(0, min(queue_size, capacity - admitted))


class CompositeAdmission(AdmissionStrategy):
    """Admit min(...) over a set of underlying strategies.

    The most restrictive strategy wins, so you can combine "max 100/s" with
    "max 5000 concurrent" without either one alone being able to overshoot.
    """

    def __init__(self, *strategies: AdmissionStrategy) -> None:
        if not strategies:
            msg = "CompositeAdmission requires at least one strategy"
            raise ValueError(msg)
        self._strategies = strategies

    def slots_available(self, *, queue_size: int, admitted: int, capacity: int) -> int:
        return min(
            s.slots_available(queue_size=queue_size, admitted=admitted, capacity=capacity)
            for s in self._strategies
        )


__all__ = [
    "CapacityAwareAdmission",
    "CompositeAdmission",
    "TimeBucketAdmission",
]
