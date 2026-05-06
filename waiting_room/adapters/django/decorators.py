"""``@waiting_room_protect("default")`` — protect a single view without middleware."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, Any
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
)
from waiting_room.core.settings import FailureMode

if TYPE_CHECKING:
    from django.http import HttpRequest


_TOKEN_COOKIE_SUFFIX = "_token"  # noqa: S105 - cookie name suffix, not a credential

ViewFn = Callable[..., HttpResponse]


def waiting_room_protect(room_name: str = "default") -> Callable[[ViewFn], ViewFn]:
    """Wrap a view so that callers without a valid admission token are queued."""

    def decorator(view: ViewFn) -> ViewFn:
        @wraps(view)
        def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            room = get_room(room_name)
            cookie_name = room.config.session_cookie_name + _TOKEN_COOKIE_SUFFIX
            token = request.COOKIES.get(cookie_name)

            if room.is_allowlisted(
                ip=client_ip(request),
                user_id=authenticated_user_id(request),
            ):
                return view(request, *args, **kwargs)

            if token:
                try:
                    room.redeem(
                        token,
                        ip=client_ip(request),
                        user_agent=user_agent(request),
                    )
                    return view(request, *args, **kwargs)
                except InvalidTokenError:
                    pass  # fall through to enqueue
                except BackendUnavailableError:
                    if room.config.failure_mode is FailureMode.FAIL_OPEN:
                        return view(request, *args, **kwargs)

            try:
                session, _ = room.enqueue(
                    ip=client_ip(request),
                    user_agent=user_agent(request),
                    user_id=authenticated_user_id(request),
                    existing_session_id=request.COOKIES.get(room.config.session_cookie_name),
                )
            except KillSwitchEngagedError:
                from django.shortcuts import render

                return render(
                    request,
                    "waiting_room/killswitch.html",
                    {"room": room.config},
                    status=503,
                )
            except BackendUnavailableError:
                if room.config.failure_mode is FailureMode.FAIL_OPEN:
                    return view(request, *args, **kwargs)
                from django.shortcuts import render

                return render(
                    request,
                    "waiting_room/unavailable.html",
                    {"room": room.config},
                    status=503,
                )

            params = urlencode(
                {"sid": session.session_id, "next": request.get_full_path()},
            )
            response = HttpResponseRedirect(f"{room.config.waiting_page_path}?{params}")
            response.set_cookie(
                room.config.session_cookie_name,
                session.session_id,
                max_age=room.config.queued_session_ttl_seconds,
                secure=room.config.cookie_secure,
                httponly=True,
                samesite=room.config.cookie_samesite,
            )
            return response

        return wrapped

    return decorator
