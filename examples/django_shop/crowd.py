"""Simulate a crowd of shoppers hitting the checkout at the same moment.

Each simulated shopper behaves like the waiting page's JavaScript: hit
``/checkout/``, get queued, poll the status endpoint, claim admission, then
load checkout, place an order and release the slot.

    python crowd.py --users 30

Standard library only, so it runs anywhere the example does.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None  # surface 302s so we can inspect the waiting-room redirect


@dataclass
class Shopper:
    base: str
    index: int
    jar: http.cookiejar.CookieJar = field(default_factory=http.cookiejar.CookieJar)
    admitted_at: float | None = None
    ordered: bool = False
    error: str | None = None

    def __post_init__(self) -> None:
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            _NoRedirect,
        )
        self._opener.addheaders = [("User-Agent", f"crowd-shopper-{self.index}")]

    def _request(self, path: str, data: dict[str, str] | None = None) -> tuple[int, str, str]:
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        try:
            with self._opener.open(f"{self.base}{path}", data=body, timeout=10) as resp:
                return resp.status, resp.headers.get("Location", ""), resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Location", ""), exc.read().decode()

    def _cookie(self, name: str) -> str:
        return next((c.value or "" for c in self.jar if c.name == name), "")

    def run(self, deadline: float) -> None:
        status, location, _ = self._request("/checkout/")
        if status != 302 or "/_waiting-room/" not in location:
            self.error = f"expected a waiting-room redirect, got {status}"
            return
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
        params = urllib.parse.urlencode({"sid": query["sid"][0], "room": query["room"][0]})

        while time.monotonic() < deadline:
            _, _, raw = self._request(f"/_waiting-room/status?{params}")
            if json.loads(raw).get("ready"):
                _, _, raw = self._request(
                    f"/_waiting-room/admit?{params}",
                    {"next": query["next"][0]},
                )
                if json.loads(raw).get("ready"):
                    self.admitted_at = time.monotonic()
                    self._checkout()
                    return
            time.sleep(0.5)
        self.error = "still waiting when --timeout ran out"

    def _checkout(self) -> None:
        status, _, page = self._request("/checkout/")
        if status != 200 or "checkout-title" not in page:
            self.error = f"checkout page returned {status}"
            return
        token = self._cookie("csrftoken")
        status, location, _ = self._request("/checkout/", {"csrfmiddlewaretoken": token})
        if status != 302 or not location.endswith("/checkout/done/"):
            self.error = f"placing the order returned {status}"
            return
        status, _, page = self._request("/checkout/done/")
        self.ordered = status == 200 and "thanks-title" in page
        if not self.ordered:
            self.error = f"order confirmation returned {status}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--users", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=120, help="seconds to keep waiting")
    args = parser.parse_args()

    shoppers = [Shopper(args.base, i) for i in range(args.users)]
    start = time.monotonic()
    deadline = start + args.timeout
    threads = [threading.Thread(target=s.run, args=(deadline,)) for s in shoppers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    admitted = sorted(s.admitted_at - start for s in shoppers if s.admitted_at is not None)
    ordered = sum(s.ordered for s in shoppers)
    errors = [f"shopper {s.index}: {s.error}" for s in shoppers if s.error]
    print(f"shoppers: {args.users}  admitted: {len(admitted)}  orders placed: {ordered}")
    if len(admitted) > 1 and admitted[-1] > admitted[0]:
        rate = (len(admitted) - 1) / (admitted[-1] - admitted[0])
        print(f"first in after {admitted[0]:.1f}s, last after {admitted[-1]:.1f}s ({rate:.2f}/s)")
    for line in errors:
        print(line)
    if errors or ordered != args.users:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
