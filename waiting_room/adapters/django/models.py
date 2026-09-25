"""Django models.

The library keeps queue and admission state in Redis (hot path). The DB model
here is purely an audit log so operators can answer "how many people did we
admit / reject in the last hour?" without scraping Redis. Persistence is
opt-in: set ``WAITING_ROOM["AUDIT_LOG"] = True`` to start writing rows.

Even when audit log is off, the table still exists — Django apps always run
their migrations. This keeps the install path predictable: one migration to
apply regardless of feature flags.
"""

from __future__ import annotations

from django.db import models


class AdmissionEvent(models.Model):
    """One row per emitted lifecycle event (enqueued, admitted, rejected, ...)."""

    EVENT_CHOICES = [
        ("enqueued", "Enqueued"),
        ("admitted", "Admitted"),
        ("redeemed", "Redeemed"),
        ("released", "Released"),
        ("expired", "Expired"),
        ("rejected", "Rejected"),
        ("kill_switch_toggled", "Kill switch toggled"),
    ]

    room = models.CharField(max_length=128, db_index=True)
    event_type = models.CharField(max_length=32, choices=EVENT_CHOICES, db_index=True)
    session_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_id = models.CharField(max_length=64, blank=True, default="")
    position = models.IntegerField(null=True, blank=True)
    queue_size = models.IntegerField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        app_label = "waiting_room"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("room", "created_at"), name="wr_event_room_created"),
            models.Index(fields=("event_type", "created_at"), name="wr_event_type_created"),
        ]

    def __str__(self) -> str:
        ts = self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else "?"
        return f"[{self.room}] {self.event_type} {self.session_id} @ {ts}"


class RoomStatus(AdmissionEvent):
    """Proxy that mounts the live room ops page in the admin (no table of its own).

    Its ``change_roomstatus`` permission gates the kill-switch toggle.
    """

    class Meta:
        proxy = True
        app_label = "waiting_room"
        verbose_name = "Room status"
        verbose_name_plural = "Room status"
