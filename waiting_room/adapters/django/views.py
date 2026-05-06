"""Built-in views: waiting page, position SSE, admit callback.

These are mounted by ``waiting_room.adapters.django.urls``. Hosts can override
the template ``waiting_room/waiting.html`` to brand the page; the JS that
ships in the default template talks to the JSON ``status`` endpoint.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from waiting_room.adapters.django.middleware import attach_admission_cookie
from waiting_room.adapters.django.registry import all_rooms, get_room
from waiting_room.core.exceptions import (
    BackendUnavailableError,
    SessionNotFoundError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.http import HttpRequest

    from waiting_room.core.engine import WaitingRoom


def _resolve_room(request: HttpRequest) -> WaitingRoom:
    room_name = request.GET.get("room") or "default"
    return get_room(room_name)


@never_cache
@require_safe
def waiting_page(request: HttpRequest) -> HttpResponse:
    """Render the waiting page. Reads ``?sid=<session_id>&next=<url>``."""
    room = _resolve_room(request)
    sid = request.GET.get("sid", "")
    if not sid:
        return HttpResponseBadRequest("missing sid")
    next_url = request.GET.get("next") or room.config.target_url
    snap = room.position(sid)
    return render(
        request,
        "waiting_room/waiting.html",
        {
            "room": room.config,
            "session_id": sid,
            "next_url": next_url,
            "position": snap.position,
            "queue_size": snap.queue_size,
            "estimated_wait_seconds": snap.estimated_wait_seconds,
            "stream_url": (
                f"{room.config.position_stream_path}?sid={sid}&room={room.config.name}"
            ),
            "admit_url": (
                f"{room.config.admit_callback_path}?sid={sid}&room={room.config.name}"
            ),
        },
    )


@never_cache
@require_safe
def position_status(request: HttpRequest) -> JsonResponse:
    """JSON polling endpoint — useful as a fallback when SSE is not desired."""
    room = _resolve_room(request)
    sid = request.GET.get("sid", "")
    if not sid:
        return JsonResponse({"error": "missing sid"}, status=400)
    return JsonResponse(dict(room.status_payload(sid)))


@never_cache
@require_safe
def position_stream(request: HttpRequest) -> StreamingHttpResponse:
    """Server-Sent Events stream of position updates.

    Streams ``{position, queue_size, ready}`` JSON events until the session is
    admitted (then sends a final ``ready`` event and closes).
    """
    room = _resolve_room(request)
    sid = request.GET.get("sid", "")
    if not sid:
        return StreamingHttpResponse(  # type: ignore[return-value]
            (b'event: error\ndata: {"error":"missing sid"}\n\n',),
            status=400,
            content_type="text/event-stream",
        )

    def event_stream() -> Iterator[bytes]:
        # Cap the lifetime of the generator so an idle client doesn't pin a worker forever.
        deadline = time.time() + 600
        last_pos = -1
        while time.time() < deadline:
            try:
                payload = room.status_payload(sid)
            except BackendUnavailableError:
                yield b'event: error\ndata: {"error":"backend_unavailable"}\n\n'
                return
            if payload["position"] != last_pos:
                last_pos = int(payload["position"])  # type: ignore[arg-type]
                yield f"data: {json.dumps(dict(payload))}\n\n".encode()
            if payload["ready"]:
                return
            time.sleep(2)

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # nginx: don't buffer SSE
    return response


@never_cache
@csrf_exempt
@require_POST
def admit_callback(request: HttpRequest) -> HttpResponse:
    """Called by the waiting page when its position is 0 — mints + sets the token."""
    room = _resolve_room(request)
    sid = request.GET.get("sid") or request.POST.get("sid")
    if not sid:
        return HttpResponseBadRequest("missing sid")
    try:
        ticket = room.try_admit(sid)
    except SessionNotFoundError:
        return JsonResponse({"error": "unknown session"}, status=404)
    except BackendUnavailableError:
        return JsonResponse({"error": "backend_unavailable"}, status=503)

    if ticket is None:
        return JsonResponse({"ready": False})

    next_url = request.POST.get("next") or room.config.target_url
    response = JsonResponse({"ready": True, "redirect": next_url})
    return attach_admission_cookie(
        response,
        room=room,
        token=ticket.token,
        ttl=int(ticket.ttl_seconds),
    )


@never_cache
@require_safe
def health(_: HttpRequest) -> JsonResponse:
    rooms = all_rooms()
    return JsonResponse(
        {
            "rooms": list(rooms),
            "ok": all(r.healthcheck() for r in rooms.values()) if rooms else False,
        },
    )


def redirect_to_target(request: HttpRequest) -> HttpResponse:
    """Used in tests/examples — redirect a fully-admitted user to ``target_url``."""
    room = _resolve_room(request)
    return HttpResponseRedirect(room.config.target_url)


