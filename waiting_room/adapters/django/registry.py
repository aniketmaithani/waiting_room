"""Process-wide registry of configured ``WaitingRoom`` instances."""

from __future__ import annotations

import threading

from django.core.exceptions import ImproperlyConfigured

from waiting_room.adapters.django.conf import load_configs
from waiting_room.core.engine import WaitingRoom

_lock = threading.Lock()
_rooms: dict[str, WaitingRoom] = {}


def build_rooms_from_settings() -> dict[str, WaitingRoom]:
    """(Re)build the registry from current Django settings. Idempotent."""
    configs = load_configs()
    with _lock:
        _rooms.clear()
        for name, cfg in configs.items():
            _rooms[name] = WaitingRoom(cfg)
    return dict(_rooms)


def get_room(name: str = "default") -> WaitingRoom:
    """Fetch a configured room. Raises if the room isn't registered."""
    try:
        return _rooms[name]
    except KeyError as exc:
        msg = (
            f"waiting room {name!r} is not configured. "
            f"Add it to settings.WAITING_ROOM and ensure the app is in INSTALLED_APPS."
        )
        raise ImproperlyConfigured(msg) from exc


def all_rooms() -> dict[str, WaitingRoom]:
    return dict(_rooms)


def register(name: str, room: WaitingRoom) -> None:
    """Inject a pre-built room (used in tests)."""
    with _lock:
        _rooms[name] = room


def reset() -> None:
    """Drop all registered rooms (used in tests)."""
    with _lock:
        _rooms.clear()
