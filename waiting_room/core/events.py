"""Lifecycle event fan-out.

Hosts register callables to react to ENQUEUED/ADMITTED/EXPIRED/etc. The
default emitter calls handlers synchronously — adapters can wrap it to push
events onto Redis pub/sub or a message bus.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from waiting_room.core.interfaces import EventEmitter

if TYPE_CHECKING:
    from waiting_room.core._types import EventType

Handler = Callable[["EventType", Mapping[str, object]], None]
_log = logging.getLogger("waiting_room.events")


class InProcessEventEmitter(EventEmitter):
    """Synchronous, in-process fan-out. Handler exceptions are logged, not raised."""

    def __init__(self) -> None:
        self._handlers: list[Handler] = []

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    def emit(self, event: EventType, payload: Mapping[str, object]) -> None:
        for h in self._handlers:
            try:
                h(event, payload)
            except Exception:
                _log.exception("waiting_room event handler %r failed", h)


__all__ = ["Handler", "InProcessEventEmitter"]
