"""``manage.py waiting_room_status`` — print queue depth + state for each room."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from waiting_room.adapters.django.registry import all_rooms


class Command(BaseCommand):
    help = "Show queue size, admitted count, and kill switch for every configured room."

    def handle(self, *args: object, **options: object) -> None:
        rooms = all_rooms()
        if not rooms:
            self.stdout.write(self.style.WARNING("No waiting rooms configured."))
            return

        header = (
            f"{'ROOM':<24} {'QUEUE':>8} {'ADMITTED':>10} {'CAPACITY':>10} {'KILL?':>6} {'OK?':>5}"
        )
        self.stdout.write(header)
        self.stdout.write("-" * len(header))
        for name, room in rooms.items():
            stats = room.stats()
            killed = "YES" if stats.kill_switch_engaged else "no"
            line = (
                f"{name:<24} {stats.queue_size:>8} {stats.admitted:>10} "
                f"{stats.capacity or 'inf':>10} {killed:>6} {'yes' if stats.healthy else 'NO':>5}"
            )
            style = self.style.ERROR if stats.kill_switch_engaged or not stats.healthy else None
            self.stdout.write(style(line) if style else self.style.SUCCESS(line))
