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
            f"{'ROOM':<24} {'QUEUE':>8} {'ADMITTED':>10} "
            f"{'CAPACITY':>10} {'KILL?':>6} {'OK?':>5}"
        )
        self.stdout.write(header)
        self.stdout.write("-" * len(header))
        for name, room in rooms.items():
            try:
                queue = room._safe_queue_size()
                admitted = room._safe_admitted_count()
                killed = "YES" if room.is_kill_switch_engaged() else "no"
                ok = "yes" if room.healthcheck() else "NO"
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"{name}: {exc}"))
                continue
            line = (
                f"{name:<24} {queue:>8} {admitted:>10} "
                f"{room.config.capacity:>10} {killed:>6} {ok:>5}"
            )
            style = self.style.ERROR if killed == "YES" else self.style.SUCCESS
            self.stdout.write(style(line))
