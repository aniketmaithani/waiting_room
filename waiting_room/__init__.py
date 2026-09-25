"""waiting_room - plug-and-play virtual waiting room for Python web apps.

Public surface: import the high-level facade and configuration helpers from here.
Framework-specific adapters live under ``waiting_room.adapters``.
"""

from waiting_room.core._types import (
    AdmissionTicket,
    EventType,
    Session,
    SessionState,
)
from waiting_room.core.engine import WaitingRoom
from waiting_room.core.exceptions import (
    BackendUnavailableError,
    InvalidTokenError,
    KillSwitchEngagedError,
    RateLimitedError,
    SessionNotFoundError,
    WaitingRoomError,
)
from waiting_room.core.settings import (
    AdmissionPolicy,
    FailureMode,
    RedisConfig,
    WaitingRoomConfig,
)

__all__ = [
    "AdmissionPolicy",
    "AdmissionTicket",
    "BackendUnavailableError",
    "EventType",
    "FailureMode",
    "InvalidTokenError",
    "KillSwitchEngagedError",
    "RateLimitedError",
    "RedisConfig",
    "Session",
    "SessionNotFoundError",
    "SessionState",
    "WaitingRoom",
    "WaitingRoomConfig",
    "WaitingRoomError",
]

__version__ = "0.1.0"
