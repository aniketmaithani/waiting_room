"""``manage.py waiting_room_flush <room>`` — DROP all queue/admission state for a room.

Used after an incident to re-open from a clean slate. Requires ``--yes`` so you
don't blow it away by accident.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from waiting_room.adapters.django.registry import get_room


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
        room = get_room(room_name)
        # The storage backend exposes the keys used by this room; we don't
        # introduce a new public API to delete them, so we touch the internals.
        backend = room._storage
        client = getattr(backend, "_client", None)
        if client is None:
            raise CommandError("storage backend has no Redis client to flush")
        prefix = f"{backend._prefix}:{{{room_name}}}:"
        keys = list(client.scan_iter(match=prefix + "*"))
        if not keys:
            self.stdout.write(f"{room_name}: nothing to flush.")
            return
        client.delete(*keys)
        self.stdout.write(self.style.WARNING(f"{room_name}: flushed {len(keys)} keys."))
