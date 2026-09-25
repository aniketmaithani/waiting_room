"""``manage.py waiting_room_kill_switch <room> on|off`` — toggle the room's kill switch."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from waiting_room.adapters.django.registry import get_room


class Command(BaseCommand):
    help = "Engage or release a room's kill switch (block / allow new entries)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("room", help="Room name as configured in WAITING_ROOM.")
        parser.add_argument(
            "state",
            choices=("on", "off"),
            help="'on' engages the kill switch (no new entries); 'off' releases it.",
        )

    def handle(self, *args: object, **options: object) -> None:
        room_name = str(options["room"])
        state = str(options["state"])
        try:
            room = get_room(room_name)
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc

        engaged = state == "on"
        room.set_kill_switch(engaged=engaged)
        verb = "engaged" if engaged else "released"
        self.stdout.write(self.style.SUCCESS(f"Kill switch {verb} for room {room_name!r}."))
