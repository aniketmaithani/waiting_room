"""Release an admitted user's capacity slot from a host view."""

from __future__ import annotations

from typing import TYPE_CHECKING

from waiting_room.adapters.django import _gate
from waiting_room.adapters.django.registry import get_room

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse


def release_admission(
    request: HttpRequest,
    response: HttpResponse,
    room_name: str = "default",
) -> bool:
    """Free the caller's slot in ``room_name`` and clear their pass cookie.

    Call it when the protected flow finishes (order placed, tickets booked) so
    the next person in line gets in sooner::

        def order_complete(request):
            response = render(request, "thanks.html")
            release_admission(request, response, "checkout")
            return response

    Returns True if a slot was released.
    """
    return _gate.release_admission(request, response, room=get_room(room_name))
