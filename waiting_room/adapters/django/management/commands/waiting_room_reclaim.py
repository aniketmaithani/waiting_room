"""``manage.py waiting_room_reclaim`` — sweep abandoned/expired sessions back to free pool."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from waiting_room.adapters.django.registry import all_rooms, get_room


class Command(BaseCommand):
    help = "Reclaim abandoned/expired sessions for one or all configured rooms."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--room",
            default=None,
            help="Reclaim only this room. Default: all configured rooms.",
        )

    def handle(self, *args: object, **options: object) -> None:
        target = options.get("room")
        try:
            rooms = {str(target): get_room(str(target))} if target else all_rooms()
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc
        for name, room in rooms.items():
            n = room.reclaim()
            self.stdout.write(f"{name}: reclaimed {n}")
