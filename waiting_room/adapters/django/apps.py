"""Django AppConfig.

On startup we:

* Build a ``WaitingRoom`` engine for each entry under ``WAITING_ROOM`` /
  ``WAITING_ROOM["ROOMS"]``.
* Register Django system checks so misconfiguration surfaces at ``manage.py
  check`` time.
* Subscribe an audit-log handler to each room's ``EventEmitter`` (a no-op
  unless ``AUDIT_LOG`` is enabled).
"""

from __future__ import annotations

from django.apps import AppConfig


class WaitingRoomConfig(AppConfig):
    name = "waiting_room.adapters.django"
    label = "waiting_room"
    verbose_name = "Virtual Waiting Room"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # Local imports so this module imports cleanly outside Django.
        from waiting_room.adapters.django import checks  # noqa: F401 — registers checks
        from waiting_room.adapters.django.registry import build_rooms_from_settings
        from waiting_room.adapters.django.signals import make_handler
        from waiting_room.core.events import InProcessEventEmitter

        rooms = build_rooms_from_settings()
        for name, room in rooms.items():
            emitter = room.emitter
            if isinstance(emitter, InProcessEventEmitter):
                emitter.subscribe(make_handler(name))
