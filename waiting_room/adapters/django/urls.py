"""URL routes mounted by ``include("waiting_room.adapters.django.urls")``.

The default mount point should match ``WAITING_ROOM["WAITING_PAGE_PATH"]``'s
parent — typically ``path("", include(...))`` since the configured paths
already start with ``/_waiting-room/``.
"""

from __future__ import annotations

from django.urls import path

from waiting_room.adapters.django import views

app_name = "waiting_room"

urlpatterns = [
    path("_waiting-room/", views.waiting_page, name="waiting"),
    path("_waiting-room/status", views.position_status, name="status"),
    path("_waiting-room/position", views.position_stream, name="stream"),
    path("_waiting-room/admit", views.admit_callback, name="admit"),
    path("_waiting-room/health", views.health, name="health"),
]
