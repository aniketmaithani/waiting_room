"""Built-in views: waiting page, position status/SSE, admit callback, health.

These are mounted by ``waiting_room.adapters.django.urls``. Hosts can override
the template ``waiting_room/waiting.html`` to brand the page; the JS that
ships in the default template polls the JSON ``status`` endpoint.

Every view takes ``?room=<name>&sid=<session_id>``. Unknown rooms answer 404
and malformed session ids 400, so bad input never surfaces as a 500.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from django.core.exceptions import ImproperlyConfigured
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotFound,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from waiting_room.adapters.django._gate import (
    render_unavailable,
    set_room_cookie,
    token_cookie_name,
)
from waiting_room.adapters.django._request import client_ip, user_agent
from waiting_room.adapters.django.registry import all_rooms, get_room
from waiting_room.core._types import is_valid_session_id
from waiting_room.core.exceptions import (
    BackendUnavailableError,
    SessionNotFoundError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.http import HttpRequest

    from waiting_room.core.engine import WaitingRoom

_STREAM_LIFETIME_SECONDS = 55
"""Cap per SSE connection; ``EventSource`` reconnects on its own afterwards."""

_STREAM_INTERVAL_SECONDS = 2


def _resolve_room(request: HttpRequest) -> WaitingRoom | None:
    try:
        return get_room(request.GET.get("room") or "default")
    except ImproperlyConfigured:
        return None


def _safe_next(candidate: str | None, room: WaitingRoom) -> str:
    """Return ``candidate`` if it is a same-site path, else the room's target URL.

    Blocks open redirects (``//evil.example``, ``https://evil.example``) and
    script URLs (``javascript:...``), which the waiting page would otherwise
    navigate to.
    """
    if (
        candidate
        and candidate.startswith("/")
        and not candidate.startswith(("//", "/\\"))
        and url_has_allowed_host_and_scheme(candidate, allowed_hosts=set())
    ):
        return candidate
    return room.config.target_url


@never_cache
@require_safe
def waiting_page(request: HttpRequest) -> HttpResponse:
    """Render the waiting page. Reads ``?room=<name>&sid=<session_id>&next=<path>``."""
    room = _resolve_room(request)
    if room is None:
        return HttpResponseNotFound("unknown room")
    sid = request.GET.get("sid", "")
    if not is_valid_session_id(sid):
        return HttpResponseBadRequest("missing or malformed sid")
    try:
        snap = room.position(sid)
    except BackendUnavailableError:
        return render_unavailable(request, room)
    query = f"sid={sid}&room={room.name}"
    return render(
        request,
        "waiting_room/waiting.html",
        {
            "room": room.config,
            "session_id": sid,
            "next_url": _safe_next(request.GET.get("next"), room),
            "position": snap.position if snap.position > 0 else None,
            "queue_size": snap.queue_size,
            "estimated_wait_seconds": snap.estimated_wait_seconds,
            "status_url": f"{reverse('waiting_room:status')}?{query}",
            "stream_url": f"{room.config.position_stream_path}?{query}",
            "admit_url": f"{room.config.admit_callback_path}?{query}",
        },
    )


@never_cache
@require_safe
def position_status(request: HttpRequest) -> JsonResponse:
    """JSON polling endpoint. Each poll also advances the queue."""
    room = _resolve_room(request)
    if room is None:
        return JsonResponse({"error": "unknown room"}, status=404)
    sid = request.GET.get("sid", "")
    if not is_valid_session_id(sid):
        return JsonResponse({"error": "missing or malformed sid"}, status=400)
    try:
        return JsonResponse(dict(room.status_payload(sid)))
    except BackendUnavailableError:
        return JsonResponse({"error": "backend_unavailable"}, status=503)


@never_cache
@require_safe
def position_stream(request: HttpRequest) -> HttpResponse:
    """Server-Sent Events stream of position updates.

    Opt-in alternative to polling for ASGI deployments. Each connection lives at
    most ``_STREAM_LIFETIME_SECONDS`` so it cannot pin a sync worker for long;
    ``EventSource`` reconnects automatically. The default template polls
    instead, because long-lived streams exhaust WSGI worker pools under load.
    """
    room = _resolve_room(request)
    if room is None:
        return HttpResponseNotFound("unknown room")
    sid = request.GET.get("sid", "")
    if not is_valid_session_id(sid):
        return HttpResponseBadRequest("missing or malformed sid")

    def event_stream() -> Iterator[bytes]:
        deadline = time.monotonic() + _STREAM_LIFETIME_SECONDS
        last: dict[str, object] | None = None
        while time.monotonic() < deadline:
            try:
                payload = dict(room.status_payload(sid))
            except BackendUnavailableError:
                yield b'event: error\ndata: {"error":"backend_unavailable"}\n\n'
                return
            if payload != last:
                last = payload
                yield f"data: {json.dumps(payload)}\n\n".encode()
            if payload["ready"] or payload["closed"] or payload["position"] == -1:
                return
            time.sleep(_STREAM_INTERVAL_SECONDS)

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # nginx: don't buffer SSE
    return response


@never_cache
@csrf_exempt
@require_POST
def admit_callback(request: HttpRequest) -> HttpResponse:
    """Mint the single-use admission ticket once the caller's turn has come.

    Only the browser holding the session cookie may claim it. The ticket is set
    as a cookie and redeemed by the gate on the next protected request.
    """
    room = _resolve_room(request)
    if room is None:
        return JsonResponse({"error": "unknown room"}, status=404)
    sid = request.GET.get("sid") or request.POST.get("sid") or ""
    if not is_valid_session_id(sid):
        return JsonResponse({"error": "missing or malformed sid"}, status=400)
    if request.COOKIES.get(room.config.session_cookie_name) != sid:
        return JsonResponse({"error": "session does not belong to this browser"}, status=403)

    try:
        ticket = room.try_admit(
            sid,
            ip=client_ip(request, trusted_proxy_count=room.config.trusted_proxy_count),
            user_agent=user_agent(request),
        )
    except SessionNotFoundError:
        return JsonResponse({"error": "unknown session"}, status=404)
    except BackendUnavailableError:
        return JsonResponse({"error": "backend_unavailable"}, status=503)

    if ticket is None:
        return JsonResponse({"ready": False})

    next_url = _safe_next(request.POST.get("next") or request.GET.get("next"), room)
    response = JsonResponse({"ready": True, "redirect": next_url})
    return set_room_cookie(
        response,
        room=room,
        name=token_cookie_name(room),
        value=ticket.token,
        max_age=int(ticket.ttl_seconds),
    )


@never_cache
@require_safe
def health(_: HttpRequest) -> JsonResponse:
    rooms = all_rooms()
    ok = bool(rooms) and all(r.healthcheck() for r in rooms.values())
    return JsonResponse({"rooms": list(rooms), "ok": ok}, status=200 if ok else 503)
