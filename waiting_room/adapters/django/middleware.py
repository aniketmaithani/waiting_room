"""``WaitingRoomMiddleware`` — protects URL prefixes declared in ``WAITING_ROOM["PROTECT"]``.

For each request, we:

1. Skip if the request path matches an internal waiting-room route or doesn't
   match a configured ``PROTECT`` prefix.
2. Resolve the room and the caller's session id from the cookie.
3. If the caller has a valid admission token cookie → let the request through.
4. Otherwise enqueue (idempotently) and 302 to the waiting page.

The middleware never raises into the host app — backend failures are converted
to fail-open or fail-closed behaviour per the engine config.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from django.http import HttpResponse, HttpResponseRedirect

from waiting_room.adapters.django._request import (
    authenticated_user_id,
    client_ip,
    user_agent,
)
from waiting_room.adapters.django.registry import get_room
from waiting_room.core.exceptions import (
    BackendUnavailableError,
    InvalidTokenError,
    KillSwitchEngagedError,
    RateLimitedError,
)
from waiting_room.core.settings import FailureMode

if TYPE_CHECKING:
    from django.http import HttpRequest

    from waiting_room.core.engine import WaitingRoom


_log = logging.getLogger("waiting_room.middleware")
_TOKEN_COOKIE_SUFFIX = "_token"  # noqa: S105 - cookie name suffix, not a credential


class WaitingRoomMiddleware:
    """Protects request paths matched by ``WAITING_ROOM["PROTECT"]`` prefixes."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        room = self._match_room(request)
        if room is None:
            return self.get_response(request)

        if self._is_internal_path(request, room):
            return self.get_response(request)

        if self._is_allowlisted(request, room):
            return self.get_response(request)

        # Already-admitted callers carry a signed token cookie.
        if self._has_valid_token(request, room):
            return self.get_response(request)

        # Honour a globally-set kill switch by *queueing*, not by 500ing.
        try:
            return self._queue_and_redirect(request, room)
        except KillSwitchEngagedError:
            return self._render_killswitched(request, room)
        except RateLimitedError:
            return HttpResponse("Too many requests", status=429, content_type="text/plain")
        except BackendUnavailableError:
            return self._fail(request, room)

    # ---- helpers -------------------------------------------------------------

    def _match_room(self, request: HttpRequest) -> WaitingRoom | None:
        from waiting_room.adapters.django.conf import protected_path_specs

        path = request.path
        for prefix, room_name in protected_path_specs():
            if path.startswith(prefix):
                try:
                    return get_room(room_name)
                except Exception:
                    _log.exception("waiting_room: cannot load room %r", room_name)
                    return None
        return None

    def _is_internal_path(self, request: HttpRequest, room: WaitingRoom) -> bool:
        cfg = room.config
        return request.path.startswith(
            (cfg.waiting_page_path, cfg.position_stream_path, cfg.admit_callback_path),
        )

    def _is_allowlisted(self, request: HttpRequest, room: WaitingRoom) -> bool:
        return room.is_allowlisted(
            ip=client_ip(request),
            user_id=authenticated_user_id(request),
        )

    def _has_valid_token(self, request: HttpRequest, room: WaitingRoom) -> bool:
        cookie_name = room.config.session_cookie_name + _TOKEN_COOKIE_SUFFIX
        token = request.COOKIES.get(cookie_name)
        if not token:
            return False
        try:
            room.redeem(token, ip=client_ip(request), user_agent=user_agent(request))
        except InvalidTokenError:
            return False
        except BackendUnavailableError:
            # Failure-mode policy applies: fail-open trusts the cookie, fail-closed re-queues.
            return room.config.failure_mode is FailureMode.FAIL_OPEN
        return True

    def _queue_and_redirect(
        self,
        request: HttpRequest,
        room: WaitingRoom,
    ) -> HttpResponse:
        cookie_name = room.config.session_cookie_name
        existing = request.COOKIES.get(cookie_name)
        session, _snap = room.enqueue(
            ip=client_ip(request),
            user_agent=user_agent(request),
            user_id=authenticated_user_id(request),
            existing_session_id=existing,
        )
        target = request.get_full_path()
        params = urlencode({"sid": session.session_id, "next": target})
        response = HttpResponseRedirect(f"{room.config.waiting_page_path}?{params}")
        response.set_cookie(
            cookie_name,
            session.session_id,
            max_age=room.config.queued_session_ttl_seconds,
            secure=room.config.cookie_secure,
            httponly=True,
            samesite=room.config.cookie_samesite,
        )
        return response

    def _render_killswitched(
        self,
        request: HttpRequest,
        room: WaitingRoom,
    ) -> HttpResponse:
        from django.shortcuts import render

        return render(
            request,
            "waiting_room/killswitch.html",
            {"room": room.config},
            status=503,
        )

    def _fail(self, request: HttpRequest, room: WaitingRoom) -> HttpResponse:
        if room.config.failure_mode is FailureMode.FAIL_OPEN:
            _log.warning("waiting_room backend unavailable; failing open")
            return self.get_response(request)
        from django.shortcuts import render

        return render(
            request,
            "waiting_room/unavailable.html",
            {"room": room.config},
            status=503,
        )


def attach_admission_cookie(
    response: HttpResponse,
    *,
    room: WaitingRoom,
    token: str,
    ttl: int,
) -> HttpResponse:
    """Helper for views that mint admission tokens — sets the ``*_token`` cookie."""
    response.set_cookie(
        room.config.session_cookie_name + _TOKEN_COOKIE_SUFFIX,
        token,
        max_age=ttl,
        secure=room.config.cookie_secure,
        httponly=True,
        samesite=room.config.cookie_samesite,
    )
    return response
