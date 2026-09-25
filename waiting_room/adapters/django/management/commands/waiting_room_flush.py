"""``manage.py waiting_room_flush <room>`` — DROP all queue/admission state for a room.

Used after an incident to re-open from a clean slate. Requires ``--yes`` so you
don't blow it away by accident.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from waiting_room.adapters.django.registry import get_room
from waiting_room.core.exceptions import BackendUnavailableError


class Command(BaseCommand):
    help = "Drop all queued + admitted sessions for a room. Destructive."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("room")
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Confirm; without this the command refuses to run.",
        )

    def handle(self, *args: object, **options: object) -> None:
        if not options.get("yes"):
            raise CommandError("Refusing to flush without --yes.")
        room_name = str(options["room"])
        try:
            room = get_room(room_name)
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc
        try:
            removed = room.flush()
        except BackendUnavailableError as exc:
            msg = f"{room_name}: storage backend unavailable: {exc}"
            raise CommandError(msg) from exc
        if not removed:
            self.stdout.write(f"{room_name}: nothing to flush.")
            return
        self.stdout.write(self.style.WARNING(f"{room_name}: flushed {removed} keys."))
