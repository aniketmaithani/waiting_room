"""Configuration dataclasses.

These are intentionally framework-free so a Django/FastAPI/Flask adapter can
build them from its own settings source. ``WaitingRoomConfig`` is the only
object the engine takes — everything else feeds into it.
"""

from __future__ import annotations

import enum
import ipaddress
from dataclasses import dataclass, field


class FailureMode(enum.StrEnum):
    """Behaviour when the storage backend is unreachable."""

    FAIL_OPEN = "fail_open"
    """Let traffic through. Suitable for soft protection (e.g. analytics gating)."""

    FAIL_CLOSED = "fail_closed"
    """Show the waiting page. Default — preserves the protection guarantee."""


class _PolicyKind(enum.StrEnum):
    TIME_BUCKET = "time_bucket"
    CAPACITY_AWARE = "capacity_aware"


@dataclass(slots=True, frozen=True)
class AdmissionPolicy:
    """How users are released from the queue.

    Construct with the classmethod factories rather than instantiating directly
    so the kind/parameters stay consistent.
    """

    kind: _PolicyKind
    admit_per_second: float = 0.0
    capacity: int = 0
    burst: int = 0

    @classmethod
    def time_bucket(cls, admit_per_second: float, *, burst: int = 0) -> AdmissionPolicy:
        """Admit at a steady rate. ``burst`` permits short spikes above the rate."""
        if admit_per_second <= 0:
            msg = "admit_per_second must be > 0"
            raise ValueError(msg)
        return cls(
            kind=_PolicyKind.TIME_BUCKET,
            admit_per_second=admit_per_second,
            burst=max(0, burst),
        )

    @classmethod
    def capacity_aware(cls, capacity: int) -> AdmissionPolicy:
        """Admit only when downstream has free slots (admitted < capacity)."""
        if capacity <= 0:
            msg = "capacity must be > 0"
            raise ValueError(msg)
        return cls(kind=_PolicyKind.CAPACITY_AWARE, capacity=capacity)


@dataclass(slots=True, frozen=True)
class RedisConfig:
    """Connection settings for the Redis-backed storage.

    The library accepts either a URL or pre-built kwargs. SSL is inferred from
    the URL scheme (``rediss://``) but can be forced via ``ssl=True``.
    """

    url: str = "redis://localhost:6379/0"
    socket_timeout: float = 1.0
    socket_connect_timeout: float = 1.0
    health_check_interval: int = 30
    max_connections: int = 64
    ssl: bool | None = None
    key_prefix: str = "wr"
    """Namespace applied to every Redis key the library writes."""


@dataclass(slots=True)
class WaitingRoomConfig:
    """Top-level configuration consumed by ``WaitingRoom``."""

    name: str
    """Logical room id — namespaces queue keys, allows multiple rooms in one app."""

    secret_key: str
    """HMAC signing key for admission tokens. Must be at least 32 bytes of entropy."""

    target_url: str
    """Where to redirect users once admitted."""

    capacity: int = 5_000
    """Maximum concurrent admitted sessions for ``time_bucket`` (``0`` = rate only).

    Combined with a ``time_bucket`` policy this gives "both": a steady drip that
    never exceeds the downstream capacity. ``capacity_aware`` policies carry
    their own capacity instead.
    """

    token_ttl_seconds: int = 300
    """How long an issued admission token remains valid."""

    queued_session_ttl_seconds: int = 1_800
    """Queued sessions that stop polling for this long are reclaimed."""

    admission_grace_seconds: int = 60
    """Window after admission during which the ticket must be redeemed.

    If the admitted user does not arrive in time, the slot is freed for the next
    person in line and the ticket is refused.
    """

    admitted_session_ttl_seconds: int = 900
    """How long an admitted user may keep using the protected area after redeeming.

    Their capacity slot is held for this long unless ``WaitingRoom.release`` is
    called earlier (e.g. once checkout completes).
    """

    storage: RedisConfig = field(default_factory=RedisConfig)
    policy: AdmissionPolicy = field(
        default_factory=lambda: AdmissionPolicy.time_bucket(admit_per_second=10.0),
    )
    failure_mode: FailureMode = FailureMode.FAIL_CLOSED

    allowlist_ips: tuple[str, ...] = ()
    """IPs or CIDR networks (e.g. ``"10.0.0.0/8"``) that bypass the queue."""

    allowlist_user_ids: tuple[str, ...] = ()
    """User identifiers that bypass the queue (VIP / staff)."""

    trusted_proxy_count: int = 0
    """Reverse proxies in front of the app that append to ``X-Forwarded-For``.

    ``0`` (default) ignores the header and uses the socket peer address, since
    any client can send it. Set to the number of proxies you run (e.g. ``1`` for
    a single load balancer) so the client address is read from the right hop.
    """

    bind_fingerprint: bool = True
    """If True, tokens are bound to IP+UA hash; tokens cannot be shared."""

    waiting_page_path: str = "/_waiting-room/"
    """Route the adapter mounts to render the waiting page."""

    position_stream_path: str = "/_waiting-room/position"
    """SSE endpoint that streams queue-position updates."""

    admit_callback_path: str = "/_waiting-room/admit"
    """Endpoint the page polls/calls when its position reaches the front."""

    session_cookie_name: str = "wr_session"
    cookie_secure: bool = True
    cookie_samesite: str = "Lax"

    rate_limit_per_ip_per_minute: int = 120
    """Caps how aggressively a single IP can re-enqueue itself."""

    def __post_init__(self) -> None:
        if not self.name:
            msg = "WaitingRoomConfig.name is required"
            raise ValueError(msg)
        if len(self.secret_key) < 32:
            msg = "WaitingRoomConfig.secret_key must be at least 32 chars"
            raise ValueError(msg)
        if not self.target_url.startswith("/") or self.target_url.startswith("//"):
            msg = "target_url must be an absolute path beginning with '/'"
            raise ValueError(msg)
        for entry in self.allowlist_ips:
            try:
                ipaddress.ip_network(entry.strip(), strict=False)
            except ValueError as exc:
                msg = f"allowlist_ips entry {entry!r} is not an IP address or network"
                raise ValueError(msg) from exc
        if self.trusted_proxy_count < 0:
            msg = "trusted_proxy_count must be >= 0"
            raise ValueError(msg)
        if self.capacity < 0:
            msg = "capacity must be >= 0"
            raise ValueError(msg)
        for attr in (
            "token_ttl_seconds",
            "queued_session_ttl_seconds",
            "admission_grace_seconds",
            "admitted_session_ttl_seconds",
        ):
            if getattr(self, attr) <= 0:
                msg = f"{attr} must be > 0"
                raise ValueError(msg)


__all__ = [
    "AdmissionPolicy",
    "FailureMode",
    "RedisConfig",
    "WaitingRoomConfig",
    "_PolicyKind",
]
