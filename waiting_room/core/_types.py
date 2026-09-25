"""Pure data types shared across the core. No I/O, no framework dependencies."""

from __future__ import annotations

import enum
import re
import time
import uuid
from dataclasses import dataclass, field


def _now() -> float:
    return time.time()


_SESSION_ID_RE = re.compile(r"[0-9a-f]{32}")


def _new_session_id() -> str:
    return uuid.uuid4().hex


def is_valid_session_id(value: str | None) -> bool:
    """Return True if ``value`` has the shape of a library-issued session id."""
    return bool(value) and _SESSION_ID_RE.fullmatch(value or "") is not None


class SessionState(enum.StrEnum):
    QUEUED = "queued"
    ADMITTED = "admitted"
    EXPIRED = "expired"
    REJECTED = "rejected"
    RELEASED = "released"


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
class AdmissionLimits:
    """Limits the storage backend enforces atomically on every admission tick."""

    capacity: int = 0
    """Maximum concurrently admitted sessions. ``0`` means unlimited."""
    rate_per_second: float = 0.0
    """Sustained admissions per second across all processes. ``0`` means unlimited."""
    burst: int = 0
    """Token-bucket size: admissions allowed in one go after an idle period."""
    grace_seconds: int = 60
    """How long an admitted session holds its slot before it must redeem."""


@dataclass(slots=True, frozen=True)
class QueuePosition:
    """Snapshot of a session's place in line."""

    position: int
    """1-indexed position. ``0`` means already admitted; ``-1`` means unknown."""
    queue_size: int
    estimated_wait_seconds: float | None
    room_closed: bool = False
    """True while the room's kill switch is engaged (nobody is being admitted)."""


@dataclass(slots=True, frozen=True)
class RoomStats:
    """Operational snapshot of a room, for dashboards and CLIs."""

    queue_size: int
    admitted: int
    capacity: int
    """Enforced concurrent admission cap (``0`` = unlimited)."""
    kill_switch_engaged: bool
    healthy: bool
