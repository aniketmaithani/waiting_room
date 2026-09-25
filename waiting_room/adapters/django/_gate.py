"""The admission gate shared by ``WaitingRoomMiddleware`` and ``@waiting_room_protect``.

For a request to a protected resource:

1. Allowlisted callers go straight through.
2. A valid *pass* cookie (reusable, fingerprint-bound, HMAC only) lets the
   request through without touching the backend.
3. A *ticket* cookie (single-use, minted by the admit endpoint) is redeemed
   once and swapped for a pass cookie on the response.
4. Everyone else is enqueued and redirected to the waiting page.

Backend failures are turned into fail-open or fail-closed behaviour per the
room's configuration; nothing here raises into the host app.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render

from waiting_room.adapters.django._request import (
    authenticated_user_id,
    client_ip,
    user_agent,
)
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


_log = logging.getLogger("waiting_room.gate")

TOKEN_COOKIE_SUFFIX = "_token"  # noqa: S105 - cookie name suffix, not a credential
PASS_COOKIE_SUFFIX = "_pass"  # noqa: S105 - cookie name suffix, not a credential


def token_cookie_name(room: WaitingRoom) -> str:
    return room.config.session_cookie_name + TOKEN_COOKIE_SUFFIX


def pass_cookie_name(room: WaitingRoom) -> str:
    return room.config.session_cookie_name + PASS_COOKIE_SUFFIX


def set_room_cookie(
    response: HttpResponse,
    *,
    room: WaitingRoom,
    name: str,
    value: str,
    max_age: int,
) -> HttpResponse:
    """Set one of the room's cookies with its configured security flags."""
    response.set_cookie(
        name,
        value,
        max_age=max(0, int(max_age)),
        secure=room.config.cookie_secure,
        httponly=True,
        samesite=room.config.cookie_samesite,
    )
    return response


def guard(
    request: HttpRequest,
    room: WaitingRoom,
    call_next: Callable[[], HttpResponse],
) -> HttpResponse:
    """Let the request through if it is admitted, otherwise send it to the queue."""
    ip = client_ip(request, trusted_proxy_count=room.config.trusted_proxy_count)
    ua = user_agent(request)

    if room.is_allowlisted(ip=ip, user_id=authenticated_user_id(request)):
        return call_next()

    admission_pass = request.COOKIES.get(pass_cookie_name(room))
    if admission_pass:
        try:
            room.verify_pass(admission_pass, ip=ip, user_agent=ua)
        except InvalidTokenError:
            pass  # expired or foreign pass: fall through to ticket/queue
        else:
            return call_next()

    ticket = request.COOKIES.get(token_cookie_name(room))
    if ticket:
        try:
            new_pass = room.redeem(ticket, ip=ip, user_agent=ua)
        except InvalidTokenError:
            pass  # spent, forged, or lapsed ticket: queue again
        except BackendUnavailableError:
            if room.config.failure_mode is FailureMode.FAIL_OPEN:
                return call_next()
        else:
            response = call_next()
            set_room_cookie(
                response,
                room=room,
                name=pass_cookie_name(room),
                value=new_pass.token,
                max_age=int(new_pass.ttl_seconds),
            )
            response.delete_cookie(
                token_cookie_name(room),
                samesite=room.config.cookie_samesite,
            )
            return response

    return _enqueue_and_redirect(request, room, ip=ip, ua=ua, call_next=call_next)


def _enqueue_and_redirect(
    request: HttpRequest,
    room: WaitingRoom,
    *,
    ip: str,
    ua: str,
    call_next: Callable[[], HttpResponse],
) -> HttpResponse:
    cookie_name = room.config.session_cookie_name
    try:
        session, _ = room.enqueue(
            ip=ip,
            user_agent=ua,
            user_id=authenticated_user_id(request),
            existing_session_id=request.COOKIES.get(cookie_name),
        )
    except RateLimitedError:
        return HttpResponse("Too many requests", status=429, content_type="text/plain")
    except KillSwitchEngagedError:
        return render_closed(request, room)
    except BackendUnavailableError:
        if room.config.failure_mode is FailureMode.FAIL_OPEN:
            _log.warning("waiting_room %s: backend unavailable, failing open", room.name)
            return call_next()
        return render_unavailable(request, room)

    params = urlencode(
        {"room": room.name, "sid": session.session_id, "next": request.get_full_path()},
    )
    response = HttpResponseRedirect(f"{room.config.waiting_page_path}?{params}")
    return set_room_cookie(
        response,
        room=room,
        name=cookie_name,
        value=session.session_id,
        max_age=room.config.queued_session_ttl_seconds,
    )


def render_closed(request: HttpRequest, room: WaitingRoom) -> HttpResponse:
    return render(request, "waiting_room/killswitch.html", {"room": room.config}, status=503)


def render_unavailable(request: HttpRequest, room: WaitingRoom) -> HttpResponse:
    return render(request, "waiting_room/unavailable.html", {"room": room.config}, status=503)


def release_admission(
    request: HttpRequest,
    response: HttpResponse,
    *,
    room: WaitingRoom,
) -> bool:
    """Free the caller's capacity slot early and clear their pass cookie.

    Call this once the protected flow is done (e.g. after checkout) so the next
    person in line is admitted sooner. Returns True if a slot was released.
    """
    token = request.COOKIES.get(pass_cookie_name(room))
    response.delete_cookie(pass_cookie_name(room), samesite=room.config.cookie_samesite)
    if not token:
        return False
    ip = client_ip(request, trusted_proxy_count=room.config.trusted_proxy_count)
    try:
        admission = room.verify_pass(token, ip=ip, user_agent=user_agent(request))
        return room.release(admission.session_id)
    except (InvalidTokenError, BackendUnavailableError):
        return False
