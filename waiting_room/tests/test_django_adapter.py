"""Django adapter — middleware, decorator, views, URL include."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import redis

if TYPE_CHECKING:
    from django.test import Client

    from waiting_room.core.engine import WaitingRoom


@pytest.fixture
def wired_room(redis_client: redis.Redis) -> WaitingRoom:
    """Build a room and inject it into the registry, replacing what startup created."""
    from waiting_room.adapters.django import registry
    from waiting_room.adapters.django.signals import make_handler
    from waiting_room.core.engine import WaitingRoom
    from waiting_room.core.events import InProcessEventEmitter
    from waiting_room.core.settings import (
        AdmissionPolicy,
        RedisConfig,
        WaitingRoomConfig,
    )

    cfg = WaitingRoomConfig(
        name="default",
        secret_key="x" * 64,
        target_url="/checkout/",
        capacity=10,
        policy=AdmissionPolicy.time_bucket(admit_per_second=100, burst=100),
        storage=RedisConfig(url="redis://fake/0"),
        rate_limit_per_ip_per_minute=1_000_000,
        cookie_secure=False,
    )
    room = WaitingRoom(cfg, redis_client=redis_client)
    if isinstance(room.emitter, InProcessEventEmitter):
        room.emitter.subscribe(make_handler("default"))
    registry.reset()
    registry.register("default", room)
    yield room
    registry.reset()


def test_protected_path_redirects_to_waiting_room(wired_room: WaitingRoom, client: Client) -> None:
    response = client.get("/checkout/", follow=False)
    assert response.status_code == 302
    assert response["Location"].startswith("/_waiting-room/?")
    assert "sid=" in response["Location"]
    assert wired_room.config.session_cookie_name in response.cookies


@pytest.mark.django_db
def test_admit_callback_mints_token_and_grants_access(
    wired_room: WaitingRoom,
    client: Client,
) -> None:
    client.get("/checkout/")
    sid = client.cookies[wired_room.config.session_cookie_name].value

    r2 = client.post(f"/_waiting-room/admit?sid={sid}&room=default")
    assert r2.status_code == 200
    body = r2.json()
    assert body["ready"] is True
    assert body["redirect"] == "/checkout/"
    assert wired_room.config.session_cookie_name + "_token" in client.cookies

    r3 = client.get("/checkout/")
    assert r3.status_code == 200
    assert r3.content == b"OK"


def test_status_endpoint_returns_position(wired_room: WaitingRoom, client: Client) -> None:
    client.get("/checkout/")
    sid = client.cookies[wired_room.config.session_cookie_name].value
    r = client.get(f"/_waiting-room/status?sid={sid}&room=default")
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == sid
    assert "position" in body
    assert "queue_size" in body


def test_killswitch_renders_503(wired_room: WaitingRoom, client: Client) -> None:
    wired_room.set_kill_switch(engaged=True)
    r = client.get("/checkout/")
    assert r.status_code == 503


def test_decorator_protects_view(wired_room: WaitingRoom, client: Client) -> None:
    r = client.get("/decorated/")
    assert r.status_code == 302
    assert r["Location"].startswith("/_waiting-room/?")


def test_health_endpoint(wired_room: WaitingRoom, client: Client) -> None:
    r = client.get("/_waiting-room/health")
    assert r.status_code == 200
    body = r.json()
    assert "default" in body["rooms"]
