"""Request-side helpers shared by the middleware and decorator."""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.http import HttpRequest


def client_ip(request: HttpRequest, *, trusted_proxy_count: int = 0) -> str:
    """Resolve the originating client IP.

    ``X-Forwarded-For`` is only honoured when ``trusted_proxy_count`` > 0. Each
    trusted proxy appends the address it received the request from, so the
    client is the entry ``trusted_proxy_count`` hops from the right; anything
    further left was supplied by the client and cannot be trusted.
    """
    remote = str(request.META.get("REMOTE_ADDR", "") or "")
    if trusted_proxy_count > 0:
        hops = [h.strip() for h in str(request.META.get("HTTP_X_FORWARDED_FOR", "")).split(",")]
        hops = [h for h in hops if h]
        if hops:
            candidate = hops[-trusted_proxy_count] if len(hops) >= trusted_proxy_count else hops[0]
            if _is_ip(candidate):
                return candidate
    return remote if _is_ip(remote) else ""


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def user_agent(request: HttpRequest) -> str:
    return str(request.META.get("HTTP_USER_AGENT", "") or "")


def authenticated_user_id(request: HttpRequest) -> str | None:
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return str(getattr(user, "pk", None) or "")
    return None
