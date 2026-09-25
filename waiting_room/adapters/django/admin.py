"""Django admin: read-only audit log + per-room kill-switch toggles.

The audit log is the standard ``ModelAdmin`` for ``AdmissionEvent``. The
kill-switch + queue overview read live from Redis; they are mounted on the
``RoomStatus`` proxy model so they show up under the ``waiting_room`` app
section in the admin index.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import admin, messages
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from waiting_room.adapters.django.models import AdmissionEvent, RoomStatus
from waiting_room.adapters.django.registry import all_rooms, get_room

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.urls import URLPattern, URLResolver


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

    def get_urls(self) -> list[URLPattern | URLResolver]:
        urls = super().get_urls()
        custom = [
            path(
                "<str:room_name>/toggle-killswitch/",
                self.admin_site.admin_view(require_POST(self.toggle_killswitch)),
                name="waiting_room_toggle_killswitch",
            ),
        ]
        return custom + urls

    def changelist_view(
        self,
        request: HttpRequest,
        extra_context: dict[str, object] | None = None,
    ) -> HttpResponse:
        if not self.has_view_permission(request):
            raise PermissionDenied
        rooms_view = []
        for name, room in all_rooms().items():
            stats = room.stats()
            rooms_view.append(
                {
                    "name": name,
                    "queue_size": stats.queue_size,
                    "admitted": stats.admitted,
                    "capacity": stats.capacity or "∞",
                    "killed": stats.kill_switch_engaged,
                    "healthy": stats.healthy,
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

    def toggle_killswitch(self, request: HttpRequest, room_name: str) -> HttpResponse:
        """Flip a room's kill switch. POST only; needs ``change_roomstatus``."""
        if not request.user.has_perm("waiting_room.change_roomstatus"):
            raise PermissionDenied
        try:
            room = get_room(room_name)
        except ImproperlyConfigured as exc:
            raise Http404(str(exc)) from exc
        engage = not room.is_kill_switch_engaged()
        room.set_kill_switch(engaged=engage)
        messages.success(
            request,
            format_html(
                "Kill switch <b>{}</b> for room <b>{}</b>.",
                "engaged" if engage else "released",
                room_name,
            ),
        )
        return HttpResponseRedirect(reverse("admin:waiting_room_roomstatus_changelist"))
