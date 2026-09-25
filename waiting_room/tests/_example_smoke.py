"""Drive the example shop end to end. Run by ``test_example.py`` in a subprocess.

It needs its own process because it boots Django with the example's settings,
not the test suite's. Redis is replaced by fakeredis.
"""

from __future__ import annotations

import os
import sys
import tempfile

import django

os.environ["DJANGO_SETTINGS_MODULE"] = "shop.settings"
os.environ["SHOP_DB"] = os.path.join(tempfile.mkdtemp(), "db.sqlite3")
django.setup()

import fakeredis  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402

from waiting_room.adapters.django import registry  # noqa: E402
from waiting_room.adapters.django.conf import load_configs  # noqa: E402
from waiting_room.core.engine import WaitingRoom  # noqa: E402

call_command("migrate", verbosity=0)
call_command("check", fail_level="WARNING")
registry.register(
    "default", WaitingRoom(load_configs()["default"], redis_client=fakeredis.FakeRedis())
)


def expect(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        sys.exit(1)


shopper = Client(HTTP_HOST="127.0.0.1")
expect(shopper.get("/").status_code == 200, "product page is public")

r = shopper.get("/checkout/")
expect(r.status_code == 302 and "/_waiting-room/" in r["Location"], "checkout is queued")
sid = shopper.cookies["wr_session"].value
query = f"sid={sid}&room=default"
expect(shopper.get(r["Location"]).status_code == 200, "waiting page renders")
expect(shopper.get(f"/_waiting-room/status?{query}").json()["ready"], "shopper is admitted")

admit = shopper.post(f"/_waiting-room/admit?{query}", {"next": "/checkout/"}).json()
expect(admit == {"ready": True, "redirect": "/checkout/"}, f"admit returns a ticket: {admit}")

page = shopper.get("/checkout/")
expect(page.status_code == 200 and b"checkout-title" in page.content, "checkout opens")
expect(shopper.get("/checkout/").status_code == 200, "checkout survives a reload")

done = shopper.post("/checkout/", {}, follow=True)
expect(b"thanks-title" in done.content, "order is placed")
expect(registry.get_room("default").stats().admitted == 0, "slot released after the order")
print("example shop OK")
