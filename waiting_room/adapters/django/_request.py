"""Request-side helpers shared by the middleware and decorator."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.http import HttpRequest


def client_ip(request: HttpRequest) -> str:
    """Resolve the originating client IP, honouring ``X-Forwarded-For``.

    The library trusts the leftmost X-F-F entry. Hosts behind a proxy without
    IP rewriting should clear the header before it reaches Django.
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.META.get("REMOTE_ADDR", "") or ""


def user_agent(request: HttpRequest) -> str:
    return request.META.get("HTTP_USER_AGENT", "") or ""


def authenticated_user_id(request: HttpRequest) -> str | None:
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return str(getattr(user, "pk", None) or "")
    return None
