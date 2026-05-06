"""Pure data types shared across the core. No I/O, no framework dependencies."""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field


def _now() -> float:
    return time.time()


def _new_session_id() -> str:
    return uuid.uuid4().hex


class SessionState(enum.StrEnum):
    QUEUED = "queued"
    ADMITTED = "admitted"
    EXPIRED = "expired"
    REJECTED = "rejected"


class EventType(enum.StrEnum):
    ENQUEUED = "enqueued"
    ADMITTED = "admitted"
    EXPIRED = "expired"
    REJECTED = "rejected"
    REDEEMED = "redeemed"
    RELEASED = "released"
    KILL_SWITCH_TOGGLED = "kill_switch_toggled"


@dataclass(slots=True, frozen=True)
class Fingerprint:
    """Identity binding for a session.

    The IP and user-agent hash are folded into the issued token so a token
    leaked to another client cannot be redeemed against the protected route.
    """

    ip: str
    user_agent_hash: str

    def as_str(self) -> str:
        return f"{self.ip}|{self.user_agent_hash}"


@dataclass(slots=True)
class Session:
    """A user waiting for (or holding) admission to a protected resource."""

    session_id: str = field(default_factory=_new_session_id)
    room: str = ""
    fingerprint: Fingerprint | None = None
    enqueued_at: float = field(default_factory=_now)
    state: SessionState = SessionState.QUEUED
    user_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class AdmissionTicket:
    """A short-lived, signed credential admitting one session past the gate."""

    token: str
    session_id: str
    room: str
    issued_at: float
    expires_at: float

    @property
    def ttl_seconds(self) -> float:
        return max(0.0, self.expires_at - _now())


@dataclass(slots=True, frozen=True)
class QueuePosition:
    """Snapshot of a session's place in line."""

    position: int
    """1-indexed position. ``0`` means already admitted; ``-1`` means unknown."""
    queue_size: int
    estimated_wait_seconds: float | None
