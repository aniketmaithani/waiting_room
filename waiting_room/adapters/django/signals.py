"""Bridge core lifecycle events to ``AdmissionEvent`` rows + Django signals.

Two integration paths:

1. ``AdmissionEvent`` is written when ``WAITING_ROOM["AUDIT_LOG"]`` is true.
2. A ``django.dispatch.Signal`` is fired for every event so hosts can subscribe
   without persisting to the DB. Use ``waiting_room_event.connect(...)``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import DatabaseError, transaction
from django.dispatch import Signal

if TYPE_CHECKING:
    from waiting_room.core._types import EventType


_log = logging.getLogger("waiting_room.signals")


waiting_room_event = Signal()
"""Fired with ``sender=room_name, event_type=str, payload=dict``."""


def _audit_enabled() -> bool:
    raw = getattr(settings, "WAITING_ROOM", {}) or {}
    rooms = raw.get("ROOMS")
    if isinstance(rooms, Mapping):
        # Multi-room: enable per-room AUDIT_LOG OR top-level fallback.
        return any(r.get("AUDIT_LOG") for r in rooms.values()) or bool(raw.get("AUDIT_LOG"))
    return bool(raw.get("AUDIT_LOG"))


def make_handler(room_name: str):
    """Build a handler closure suitable for ``room.emitter.subscribe``."""

    def handler(event: EventType, payload: Mapping[str, object]) -> None:
        try:
            waiting_room_event.send(
                sender=room_name,
                event_type=str(event),
                payload=dict(payload),
            )
        except Exception:
            _log.exception("waiting_room signal dispatch failed")

        if not _audit_enabled():
            return

        # Lazy import — keeps importing this module safe before apps are ready.
        from waiting_room.adapters.django.models import AdmissionEvent

        ip = payload.get("ip")
        try:
            with transaction.atomic():
                AdmissionEvent.objects.create(
                    room=room_name,
                    event_type=str(event),
                    session_id=str(payload.get("session_id") or ""),
                    ip=ip if isinstance(ip, str) and ip else None,
                    user_id=str(payload.get("user_id") or ""),
                    position=_safe_int(payload.get("position")),
                    queue_size=_safe_int(payload.get("size")),
                    payload={k: v for k, v in payload.items() if _json_safe(v)},
                )
        except DatabaseError:
            _log.exception("waiting_room: failed to persist AdmissionEvent")

    return handler


def _safe_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _json_safe(value: object) -> bool:
    return isinstance(value, (str, int, float, bool, type(None), list, dict))
