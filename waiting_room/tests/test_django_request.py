"""Client identity helpers."""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from waiting_room.adapters.django._request import client_ip


@pytest.fixture
def rf() -> RequestFactory:
    return RequestFactory()


def test_forwarded_for_is_ignored_without_trusted_proxies(rf: RequestFactory) -> None:
    request = rf.get("/", HTTP_X_FORWARDED_FOR="10.0.0.5", REMOTE_ADDR="203.0.113.9")
    assert client_ip(request) == "203.0.113.9"


def test_rightmost_untrusted_hop_is_used(rf: RequestFactory) -> None:
    # Client spoofs "10.0.0.5"; our single load balancer appends the real peer.
    request = rf.get(
        "/",
        HTTP_X_FORWARDED_FOR="10.0.0.5, 198.51.100.7",
        REMOTE_ADDR="192.0.2.1",
    )
    assert client_ip(request, trusted_proxy_count=1) == "198.51.100.7"
    assert client_ip(request, trusted_proxy_count=2) == "10.0.0.5"


def test_garbage_forwarded_for_falls_back_to_peer(rf: RequestFactory) -> None:
    request = rf.get("/", HTTP_X_FORWARDED_FOR="<script>", REMOTE_ADDR="192.0.2.1")
    assert client_ip(request, trusted_proxy_count=1) == "192.0.2.1"
