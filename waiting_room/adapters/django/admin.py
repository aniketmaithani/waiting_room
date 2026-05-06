"""Django admin: read-only audit log + per-room kill-switch toggles.

The audit log is the standard ``ModelAdmin`` for ``AdmissionEvent``. The
kill-switch + queue overview live on a separate admin URL we register, since
they aren't backed by a model — they read live from Redis. We mount that ops
page using a proxy of ``AdmissionEvent`` so it shows up under the
``waiting_room`` app section in the admin index.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from waiting_room.adapters.django.models import AdmissionEvent
from waiting_room.adapters.django.registry import all_rooms, get_room

if TYPE_CHECKING:
    from django.http import HttpRequest


@admin.register(AdmissionEvent)
class AdmissionEventAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "room",
        "event_type",
        "session_id",
        "ip",
        "position",
        "queue_size",
    )
    list_filter = ("room", "event_type", "created_at")
    search_fields = ("session_id", "ip", "user_id")
    readonly_fields = tuple(f.name for f in AdmissionEvent._meta.fields)
    ordering = ("-created_at",)
    date_hierarchy = "created_at"

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self,
        request: HttpRequest,
        obj: AdmissionEvent | None = None,
    ) -> bool:
        return False


class RoomStatus(AdmissionEvent):  # type: ignore[misc, valid-type]
    """Proxy model used purely to mount the ops view under the waiting_room admin app."""

    class Meta:
        proxy = True
        app_label = "waiting_room"
        verbose_name = "Room status"
        verbose_name_plural = "Room status"


@admin.register(RoomStatus)
class RoomStatusAdmin(admin.ModelAdmin):
    """Live ops dashboard — queue depth, admitted count, kill-switch toggle."""

    change_list_template = "waiting_room/admin_room_ops.html"

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self,
        request: HttpRequest,
        obj: AdmissionEvent | None = None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: AdmissionEvent | None = None,
    ) -> bool:
        return False

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<str:room_name>/toggle-killswitch/",
                self.admin_site.admin_view(self.toggle_killswitch),
                name="waiting_room_toggle_killswitch",
            ),
        ]
        return custom + urls

    def changelist_view(self, request: HttpRequest, extra_context=None):
        rooms_view = []
        for name, room in all_rooms().items():
            try:
                queue_size = room._safe_queue_size()
                admitted = room._safe_admitted_count()
                killed = room.is_kill_switch_engaged()
                ok = room.healthcheck()
            except Exception as exc:
                queue_size = admitted = 0
                killed = False
                ok = False
                messages.error(request, f"room {name}: {exc}")
            rooms_view.append(
                {
                    "name": name,
                    "queue_size": queue_size,
                    "admitted": admitted,
                    "capacity": room.config.capacity,
                    "killed": killed,
                    "healthy": ok,
                    "toggle_url": reverse(
                        "admin:waiting_room_toggle_killswitch",
                        args=[name],
                    ),
                },
            )
        ctx = {
            **self.admin_site.each_context(request),
            "title": "Waiting rooms",
            "rooms": rooms_view,
        }
        if extra_context:
            ctx.update(extra_context)
        return TemplateResponse(request, self.change_list_template, ctx)

    def toggle_killswitch(self, request: HttpRequest, room_name: str):
        room = get_room(room_name)
        currently = room.is_kill_switch_engaged()
        room.set_kill_switch(engaged=not currently)
        verb = "engaged" if not currently else "released"
        messages.success(
            request,
            format_html("Kill switch <b>{}</b> for room <b>{}</b>.", verb, room_name),
        )
        return HttpResponseRedirect(reverse("admin:waiting_room_roomstatus_changelist"))
