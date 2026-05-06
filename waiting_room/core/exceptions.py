"""Exception hierarchy for the waiting room.

Adapters should catch ``WaitingRoomError`` to handle every library-raised
condition. Adapters should not catch the storage backend's underlying
exceptions directly — the engine wraps them in ``BackendUnavailableError``.
"""

from __future__ import annotations


class WaitingRoomError(Exception):
    """Base for all library-raised exceptions."""


class BackendUnavailableError(WaitingRoomError):
    """Raised when the storage backend (e.g. Redis) is unreachable."""


class InvalidTokenError(WaitingRoomError):
    """Raised when an admission token fails verification."""


class TokenExpiredError(InvalidTokenError):
    """Raised when an otherwise valid token is past its TTL."""


class TokenAlreadyUsedError(InvalidTokenError):
    """Raised when a single-use token is presented twice."""


class TokenFingerprintMismatchError(InvalidTokenError):
    """Raised when the token was issued for a different session fingerprint."""


class SessionNotFoundError(WaitingRoomError):
    """Raised when a referenced session does not exist (or has expired)."""


class KillSwitchEngagedError(WaitingRoomError):
    """Raised when the kill switch is on and the room rejects new entries."""


class CapacityExceededError(WaitingRoomError):
    """Raised when no further admissions are allowed for the current window."""
