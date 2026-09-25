"""Admin ops page and kill-switch toggle."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.contrib.auth.models import Permission, User

if TYPE_CHECKING:
    from django.test import Client

    from waiting_room.core.engine import WaitingRoom

_TOGGLE = "/admin/waiting_room/roomstatus/default/toggle-killswitch/"


@pytest.fixture
def staff(client: Client) -> User:
    user = User.objects.create_user("ops", password="pw", is_staff=True)
    client.force_login(user)
    return user


@pytest.mark.django_db
def test_toggle_requires_post(wired_room: WaitingRoom, client: Client, admin_user: User) -> None:
    client.force_login(admin_user)
    assert client.get(_TOGGLE).status_code == 405
    assert wired_room.is_kill_switch_engaged() is False


@pytest.mark.django_db
def test_toggle_requires_permission(wired_room: WaitingRoom, client: Client, staff: User) -> None:
    assert client.post(_TOGGLE).status_code == 403
    assert wired_room.is_kill_switch_engaged() is False


@pytest.mark.django_db
def test_toggle_with_permission(wired_room: WaitingRoom, client: Client, staff: User) -> None:
    staff.user_permissions.add(Permission.objects.get(codename="change_roomstatus"))
    assert client.post(_TOGGLE).status_code == 302
    assert wired_room.is_kill_switch_engaged() is True


@pytest.mark.django_db
def test_ops_page_renders(wired_room: WaitingRoom, client: Client, admin_user: User) -> None:
    client.force_login(admin_user)
    r = client.get("/admin/waiting_room/roomstatus/")
    assert r.status_code == 200
    assert b"default" in r.content
