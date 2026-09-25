"""Django adapter — middleware, decorator, views, URL include."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from waiting_room.core.interfaces import RateLimiter

if TYPE_CHECKING:
    from django.test import Client

    from waiting_room.core.engine import WaitingRoom


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


def test_rate_limited_client_gets_429(wired_room: WaitingRoom, client: Client) -> None:
    wired_room._rate_limiter = _Deny()
    r = client.get("/checkout/")
    assert r.status_code == 429


class _Deny(RateLimiter):
    def acquire(self, key: str) -> bool:
        return False


def test_spoofed_forwarded_for_does_not_bypass_allowlist(
    wired_room: WaitingRoom,
    client: Client,
) -> None:
    wired_room.config.allowlist_ips = ("10.0.0.5",)
    r = client.get("/checkout/", HTTP_X_FORWARDED_FOR="10.0.0.5")
    assert r.status_code == 302


def _queue_and_admit(client: Client, room: WaitingRoom, path: str = "/checkout/") -> None:
    r = client.get(path)
    assert r.status_code == 302
    sid = client.cookies[room.config.session_cookie_name].value
    status = client.get(f"/_waiting-room/status?sid={sid}&room=default").json()
    assert status["ready"] is True
    admit = client.post(f"/_waiting-room/admit?sid={sid}&room=default")
    assert admit.json()["ready"] is True


@pytest.mark.django_db
def test_admitted_user_can_make_many_requests(wired_room: WaitingRoom, client: Client) -> None:
    _queue_and_admit(client, wired_room)
    first = client.get("/checkout/")
    assert first.status_code == 200
    assert "wr_session_pass" in first.cookies
    assert first.cookies["wr_session_token"].value == ""  # ticket spent and cleared
    for _ in range(3):
        assert client.get("/checkout/").status_code == 200
    assert client.post("/checkout/").status_code == 200


@pytest.mark.django_db
def test_decorated_view_admits_for_many_requests(wired_room: WaitingRoom, client: Client) -> None:
    _queue_and_admit(client, wired_room, "/decorated/")
    assert client.get("/decorated/").content == b"DECORATED"
    assert client.get("/decorated/").content == b"DECORATED"


def test_redirect_carries_room_name(wired_room: WaitingRoom, client: Client) -> None:
    r = client.get("/checkout/")
    assert "room=default" in r["Location"]


@pytest.mark.django_db
def test_stolen_pass_is_useless_elsewhere(wired_room: WaitingRoom, client: Client) -> None:
    from django.test import Client as DjangoClient

    _queue_and_admit(client, wired_room)
    client.get("/checkout/")
    stolen = client.cookies["wr_session_pass"].value
    thief = DjangoClient(REMOTE_ADDR="203.0.113.66")
    thief.cookies["wr_session_pass"] = stolen
    assert thief.get("/checkout/").status_code == 302


@pytest.mark.django_db
def test_release_admission_frees_slot(wired_room: WaitingRoom, client: Client) -> None:
    from django.http import HttpResponse
    from django.test import RequestFactory

    from waiting_room.adapters.django import release_admission

    _queue_and_admit(client, wired_room)
    client.get("/checkout/")
    assert wired_room.stats().admitted == 1
    request = RequestFactory().get("/done/")
    request.COOKIES["wr_session_pass"] = client.cookies["wr_session_pass"].value
    response = HttpResponse()
    assert release_admission(request, response) is True
    assert wired_room.stats().admitted == 0
    assert response.cookies["wr_session_pass"].value == ""


def _break_backend(room: WaitingRoom, monkeypatch: pytest.MonkeyPatch) -> None:
    from waiting_room.core.exceptions import BackendUnavailableError

    def _down(*_args: object, **_kwargs: object) -> None:
        raise BackendUnavailableError

    monkeypatch.setattr(room._storage, "enqueue", _down)


def test_backend_down_fails_closed_by_default(
    wired_room: WaitingRoom,
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _break_backend(wired_room, monkeypatch)
    assert client.get("/checkout/").status_code == 503


def test_backend_down_fails_open_when_configured(
    wired_room: WaitingRoom,
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from waiting_room.core.settings import FailureMode

    wired_room.config.failure_mode = FailureMode.FAIL_OPEN
    _break_backend(wired_room, monkeypatch)
    r = client.get("/checkout/")
    assert r.status_code == 200
    assert r.content == b"OK"


def _sid(client: Client, room: WaitingRoom) -> str:
    client.get("/checkout/")
    return str(client.cookies[room.config.session_cookie_name].value)


@pytest.mark.parametrize(
    "evil",
    ["javascript:alert(1)", "//evil.example/", "https://evil.example/", "/\\evil.example"],
)
def test_admit_rejects_unsafe_next(wired_room: WaitingRoom, client: Client, evil: str) -> None:
    sid = _sid(client, wired_room)
    client.get(f"/_waiting-room/status?sid={sid}&room=default")
    r = client.post(f"/_waiting-room/admit?sid={sid}&room=default", {"next": evil})
    assert r.json()["redirect"] == "/checkout/"


def test_admit_keeps_safe_next(wired_room: WaitingRoom, client: Client) -> None:
    sid = _sid(client, wired_room)
    r = client.post(f"/_waiting-room/admit?sid={sid}&room=default", {"next": "/checkout/?item=7"})
    assert r.json()["redirect"] == "/checkout/?item=7"


def test_waiting_page_escapes_unsafe_next(wired_room: WaitingRoom, client: Client) -> None:
    sid = _sid(client, wired_room)
    r = client.get(f"/_waiting-room/?sid={sid}&room=default&next=javascript:alert(1)")
    assert r.status_code == 200
    assert b"javascript:" not in r.content


def test_admit_requires_the_session_cookie(wired_room: WaitingRoom, client: Client) -> None:
    from django.test import Client as DjangoClient

    sid = _sid(client, wired_room)
    r = DjangoClient().post(f"/_waiting-room/admit?sid={sid}&room=default")
    assert r.status_code == 403


def test_unknown_room_is_404(wired_room: WaitingRoom, client: Client) -> None:
    sid = "a" * 32
    assert client.get(f"/_waiting-room/?sid={sid}&room=nope").status_code == 404
    assert client.get(f"/_waiting-room/status?sid={sid}&room=nope").status_code == 404


def test_malformed_sid_is_400(wired_room: WaitingRoom, client: Client) -> None:
    assert client.get("/_waiting-room/status?sid=../../x&room=default").status_code == 400
    assert client.get("/_waiting-room/?sid=&room=default").status_code == 400


def test_waiting_page_polls_status_endpoint(wired_room: WaitingRoom, client: Client) -> None:
    sid = _sid(client, wired_room)
    r = client.get(f"/_waiting-room/?sid={sid}&room=default&next=/checkout/")
    assert r.status_code == 200
    html = r.content.decode()
    assert f'data-status="/_waiting-room/status?sid={sid}&amp;room=default"' in html
    assert 'data-next="/checkout/"' in html
    assert "EventSource" not in html
