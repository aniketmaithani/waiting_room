"""URLConf used by the Django adapter test suite."""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path

from waiting_room.adapters.django.decorators import waiting_room_protect


def _checkout(_request):  # type: ignore[no-untyped-def]
    return HttpResponse(b"OK")


@waiting_room_protect("default")
def _decorated(_request):  # type: ignore[no-untyped-def]
    return HttpResponse(b"DECORATED")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("waiting_room.adapters.django.urls")),
    path("checkout/", _checkout),
    path("decorated/", _decorated),
]
