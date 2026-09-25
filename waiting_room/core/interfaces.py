"""Pluggable interfaces.

Each ABC defines one extension point. Concrete implementations live next to
the interface they implement (e.g. ``storage.redis_backend.RedisStorageBackend``).
The core ``WaitingRoom`` engine depends only on these abstractions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from waiting_room.core._types import (
        AdmissionLimits,
        AdmissionTicket,
        EventType,
        QueuePosition,
        Session,
    )


class StorageBackend(ABC):
    """Persistence + atomic queue operations for a single waiting room.

    Implementations must guarantee that ``admit_batch`` and ``enqueue`` are
    atomic with respect to each other under concurrent access. The Redis
    implementation does this through Lua scripts.
    """

    @abstractmethod
    def enqueue(
        self,
        room: str,
        session: Session,
        score: float,
        *,
        ttl_seconds: int = 1_800,
        check_fingerprint: bool = True,
    ) -> int:
        """Insert ``session`` at ``score`` (idempotent) and return its 1-indexed position.

        Session metadata lives for ``ttl_seconds`` unless refreshed by ``position``.
        Special results: ``0`` the session is already admitted; ``-1`` the kill
        switch is engaged; ``-2`` the id exists but was created by a different
        client fingerprint (only when ``check_fingerprint``), so the caller must
        start a fresh session rather than take over someone else's place.
        """

    @abstractmethod
    def position(self, room: str, session_id: str, *, ttl_seconds: int = 0) -> QueuePosition:
        """Return the session's position: ``0`` if admitted, ``-1`` if unknown.

        With ``ttl_seconds > 0`` a queued session is also marked as seen now and
        its metadata TTL is refreshed, so active waiters are never reclaimed.
        """

    @abstractmethod
    def queue_size(self, room: str) -> int:
        """Number of sessions currently queued."""

    @abstractmethod
    def admit_batch(self, room: str, n: int, *, limits: AdmissionLimits) -> list[str]:
        """Atomically admit up to ``n`` sessions from the front of the queue.

        Implementations must, in one atomic step: free admission slots whose
        deadline has passed, admit nobody while the kill switch is engaged, and
        never exceed ``limits.capacity`` or ``limits.rate_per_second`` no matter
        how many processes tick concurrently. Returns the admitted session ids.
        """

    @abstractmethod
    def remove(self, room: str, session_id: str) -> bool:
        """Remove a session from the queue (e.g. abandon). Returns True if removed."""

    @abstractmethod
    def get_session(self, room: str, session_id: str) -> Session | None:
        """Return session metadata if it exists in this room."""

    @abstractmethod
    def update_session(
        self,
        room: str,
        session_id: str,
        fields: Mapping[str, str],
    ) -> None:
        """Patch a session's metadata fields."""

    @abstractmethod
    def admitted_count(self, room: str) -> int:
        """Number of currently-admitted sessions (i.e. consuming a capacity slot)."""

    @abstractmethod
    def release_admission(self, room: str, session_id: str) -> bool:
        """Free a capacity slot held by an admitted session."""

    @abstractmethod
    def reclaim_expired(self, room: str, before_ts: float) -> int:
        """Drop sessions idle since ``before_ts`` and expired admissions. Returns count."""

    @abstractmethod
    def set_kill_switch(self, room: str, *, engaged: bool) -> None:
        """Toggle the kill switch. When engaged, the engine refuses new entries."""

    @abstractmethod
    def is_kill_switch_engaged(self, room: str) -> bool:
        """Read the current kill-switch state."""

    @abstractmethod
    def ping(self) -> bool:
        """Best-effort liveness check for the underlying store."""


class TokenSigner(ABC):
    """Mints and validates short-lived, purpose-scoped admission tokens.

    ``purpose`` separates token kinds signed with the same key: the engine
    uses ``"admit"`` for the single-use ticket handed out of the queue and
    ``"pass"`` for the reusable credential an admitted user carries.
    """

    @abstractmethod
    def issue(
        self,
        *,
        session_id: str,
        room: str,
        fingerprint: str,
        ttl_seconds: int,
        purpose: str = "admit",
    ) -> AdmissionTicket: ...

    @abstractmethod
    def verify(
        self,
        token: str,
        *,
        fingerprint: str,
        purpose: str = "admit",
    ) -> AdmissionTicket:
        """Validate signature, purpose, expiry, and fingerprint binding. Raises on failure."""

    @abstractmethod
    def mark_used(self, token: str) -> bool:
        """Record single-use redemption. Raises ``TokenAlreadyUsedError`` on reuse."""


class AdmissionStrategy(ABC):
    """Decides how many sessions to admit on each tick."""

    @abstractmethod
    def slots_available(self, *, queue_size: int, admitted: int, capacity: int) -> int:
        """How many sessions can be admitted right now."""


class SessionStore(ABC):
    """Out-of-band session state (e.g. user attached metadata).

    Many deployments use the same Redis as ``StorageBackend``; this interface
    exists so a host can plug in a different store (e.g. DynamoDB) for session
    metadata while keeping the queue in Redis.
    """

    @abstractmethod
    def save(self, session: Session, ttl_seconds: int) -> None: ...

    @abstractmethod
    def load(self, session_id: str) -> Session | None: ...

    @abstractmethod
    def delete(self, session_id: str) -> None: ...


class EventEmitter(ABC):
    """Fan-out hook for queue lifecycle events."""

    @abstractmethod
    def emit(self, event: EventType, payload: Mapping[str, object]) -> None: ...


class RateLimiter(ABC):
    """Per-key rate limiter — used to throttle abusive enqueue attempts."""

    @abstractmethod
    def acquire(self, key: str) -> bool:
        """Return True if a token is available, False otherwise."""


class MetricsRecorder(ABC):
    """Adapter to a metrics backend (Prometheus, OpenTelemetry, statsd, …)."""

    @abstractmethod
    def incr(self, name: str, value: float = 1.0, **labels: str) -> None: ...

    @abstractmethod
    def observe(self, name: str, value: float, **labels: str) -> None: ...

    @abstractmethod
    def gauge(self, name: str, value: float, **labels: str) -> None: ...


__all__ = [
    "AdmissionStrategy",
    "EventEmitter",
    "MetricsRecorder",
    "RateLimiter",
    "SessionStore",
    "StorageBackend",
    "TokenSigner",
]
