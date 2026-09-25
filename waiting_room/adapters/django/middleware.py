"""``WaitingRoomMiddleware`` — protects URL prefixes declared in ``WAITING_ROOM["PROTECT"]``.

For each request, we:

1. Skip if the path doesn't match a configured ``PROTECT`` prefix, or is one of
   the waiting room's own routes.
2. Hand the request to the shared admission gate (see ``_gate.guard``): admitted
   callers pass, everyone else is queued and redirected to the waiting page.

The middleware never raises into the host app — backend failures are converted
to fail-open or fail-closed behaviour per the engine config.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from waiting_room.adapters.django._gate import guard
from waiting_room.adapters.django.conf import protected_path_specs
from waiting_room.adapters.django.registry import get_room

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

    from waiting_room.core.engine import WaitingRoom


_log = logging.getLogger("waiting_room.middleware")


class WaitingRoomMiddleware:
    """Protects request paths matched by ``WAITING_ROOM["PROTECT"]`` prefixes."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        room = self._match_room(request)
        if room is None or self._is_internal_path(request, room):
            return self.get_response(request)
        return guard(request, room, lambda: self.get_response(request))

    def _match_room(self, request: HttpRequest) -> WaitingRoom | None:
        path = request.path
        for prefix, room_name in protected_path_specs():
            if path.startswith(prefix):
                try:
                    return get_room(room_name)
                except Exception:
                    _log.exception("waiting_room: cannot load room %r", room_name)
                    return None
        return None

    @staticmethod
    def _is_internal_path(request: HttpRequest, room: WaitingRoom) -> bool:
        cfg = room.config
        return request.path.startswith(
            (cfg.waiting_page_path, cfg.position_stream_path, cfg.admit_callback_path),
        )
