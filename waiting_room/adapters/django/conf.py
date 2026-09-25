"""Read the ``WAITING_ROOM`` Django setting and produce ``WaitingRoomConfig`` objects.

Accepted shapes:

* dict with ``ROOMS`` key → multi-room: ``{"ROOMS": {"<name>": {...}, ...}}``
* dict without ``ROOMS`` → single-room shorthand: treated as a single config
  using the dict's ``NAME`` field as the room id (or ``"default"``).

Each room dict mirrors ``WaitingRoomConfig`` with upper-case keys, e.g.::

    WAITING_ROOM = {
        "SECRET_KEY": "...",
        "TARGET_URL": "/checkout",
        "CAPACITY": 5000,
        "POLICY": {"KIND": "time_bucket", "ADMIT_PER_SECOND": 100},
        "REDIS": {"URL": "redis://...", "KEY_PREFIX": "wr"},
        "ALLOWLIST_IPS": ["10.0.0.1"],
    }
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from waiting_room.core.settings import (
    AdmissionPolicy,
    FailureMode,
    RedisConfig,
    WaitingRoomConfig,
)

_DEFAULT_ROOM = "default"


def _coerce_policy(raw: Mapping[str, Any] | None, *, room_capacity: int) -> AdmissionPolicy:
    if not raw:
        return AdmissionPolicy.time_bucket(admit_per_second=10.0)
    kind = str(raw.get("KIND", "time_bucket")).lower()
    if kind == "time_bucket":
        return AdmissionPolicy.time_bucket(
            admit_per_second=float(raw.get("ADMIT_PER_SECOND", 10.0)),
            burst=int(raw.get("BURST", 0)),
        )
    if kind == "capacity_aware":
        # POLICY.CAPACITY wins; otherwise fall back to the room-level CAPACITY.
        return AdmissionPolicy.capacity_aware(capacity=int(raw.get("CAPACITY", room_capacity)))
    msg = f"unknown WAITING_ROOM POLICY KIND: {kind!r}"
    raise ImproperlyConfigured(msg)


def _coerce_redis(raw: Mapping[str, Any] | None) -> RedisConfig:
    raw = raw or {}
    return RedisConfig(
        url=str(raw.get("URL", getattr(settings, "REDIS_URL", "redis://localhost:6379/0"))),
        socket_timeout=float(raw.get("SOCKET_TIMEOUT", 1.0)),
        socket_connect_timeout=float(raw.get("SOCKET_CONNECT_TIMEOUT", 1.0)),
        max_connections=int(raw.get("MAX_CONNECTIONS", 64)),
        key_prefix=str(raw.get("KEY_PREFIX", "wr")),
    )


def _coerce_failure_mode(raw: str | None) -> FailureMode:
    if raw is None:
        return FailureMode.FAIL_CLOSED
    try:
        return FailureMode(raw)
    except ValueError as exc:
        msg = f"invalid WAITING_ROOM FAILURE_MODE {raw!r}"
        raise ImproperlyConfigured(msg) from exc


def _build_one(name: str, raw: Mapping[str, Any]) -> WaitingRoomConfig:
    try:
        secret = str(raw["SECRET_KEY"])
        target = str(raw["TARGET_URL"])
    except KeyError as exc:
        missing = exc.args[0]
        msg = f"WAITING_ROOM[{name!r}] missing required {missing!r}"
        raise ImproperlyConfigured(msg) from exc

    capacity = int(raw.get("CAPACITY", 5_000))
    try:
        policy = _coerce_policy(raw.get("POLICY"), room_capacity=capacity)
    except (TypeError, ValueError) as exc:
        msg = f"WAITING_ROOM[{name!r}] POLICY is invalid: {exc}"
        raise ImproperlyConfigured(msg) from exc

    return WaitingRoomConfig(
        name=name,
        secret_key=secret,
        target_url=target,
        capacity=capacity,
        token_ttl_seconds=int(raw.get("TOKEN_TTL_SECONDS", 300)),
        queued_session_ttl_seconds=int(raw.get("QUEUED_SESSION_TTL_SECONDS", 1_800)),
        admission_grace_seconds=int(raw.get("ADMISSION_GRACE_SECONDS", 60)),
        storage=_coerce_redis(raw.get("REDIS")),
        policy=policy,
        failure_mode=_coerce_failure_mode(raw.get("FAILURE_MODE")),
        allowlist_ips=tuple(raw.get("ALLOWLIST_IPS") or ()),
        allowlist_user_ids=tuple(raw.get("ALLOWLIST_USER_IDS") or ()),
        bind_fingerprint=bool(raw.get("BIND_FINGERPRINT", True)),
        waiting_page_path=str(raw.get("WAITING_PAGE_PATH", "/_waiting-room/")),
        position_stream_path=str(raw.get("POSITION_STREAM_PATH", "/_waiting-room/position")),
        admit_callback_path=str(raw.get("ADMIT_CALLBACK_PATH", "/_waiting-room/admit")),
        session_cookie_name=str(raw.get("SESSION_COOKIE_NAME", "wr_session")),
        cookie_secure=bool(raw.get("COOKIE_SECURE", not settings.DEBUG)),
        cookie_samesite=str(raw.get("COOKIE_SAMESITE", "Lax")),
        rate_limit_per_ip_per_minute=int(raw.get("RATE_LIMIT_PER_IP_PER_MINUTE", 120)),
    )


def load_configs() -> dict[str, WaitingRoomConfig]:
    raw = getattr(settings, "WAITING_ROOM", None)
    if not raw:
        return {}
    if not isinstance(raw, Mapping):
        msg = "WAITING_ROOM setting must be a mapping"
        raise ImproperlyConfigured(msg)

    rooms = raw.get("ROOMS")
    if rooms is None:
        # Single-room shorthand.
        name = str(raw.get("NAME", _DEFAULT_ROOM))
        return {name: _build_one(name, raw)}

    if not isinstance(rooms, Mapping):
        msg = "WAITING_ROOM['ROOMS'] must be a mapping of name -> config"
        raise ImproperlyConfigured(msg)
    return {str(n): _build_one(str(n), v) for n, v in rooms.items()}


def protected_path_specs() -> Iterable[tuple[str, str]]:
    """Yield ``(url_prefix, room_name)`` pairs from ``WAITING_ROOM["PROTECT"]``.

    Example::

        WAITING_ROOM["PROTECT"] = [("/checkout/", "default"), ("/api/orders", "api")]
    """
    raw = getattr(settings, "WAITING_ROOM", None) or {}
    for prefix, room in raw.get("PROTECT", []) or []:
        yield str(prefix), str(room)
