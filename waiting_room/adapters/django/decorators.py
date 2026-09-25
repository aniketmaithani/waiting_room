"""``@waiting_room_protect("default")`` — protect a single view without middleware."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, Any

from waiting_room.adapters.django._gate import guard
from waiting_room.adapters.django.registry import get_room

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse


ViewFn = Callable[..., "HttpResponse"]


def waiting_room_protect(room_name: str = "default") -> Callable[[ViewFn], ViewFn]:
    """Wrap a view so that callers without a valid admission pass are queued."""

    def decorator(view: ViewFn) -> ViewFn:
        @wraps(view)
        def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            return guard(request, get_room(room_name), lambda: view(request, *args, **kwargs))

        return wrapped

    return decorator
