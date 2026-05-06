"""System checks (``manage.py check``)."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.checks import Error, Warning
from django.test import override_settings

from waiting_room.adapters.django.checks import check_waiting_room_settings


def _ids(issues: list[Error | Warning]) -> set[str]:
    return {i.id for i in issues}


@override_settings(WAITING_ROOM=None)
def test_warns_when_setting_missing() -> None:
    issues: Any = check_waiting_room_settings(app_configs=None)
    assert "waiting_room.W001" in _ids(issues)


@override_settings(
    WAITING_ROOM={
        "SECRET_KEY": "short",
        "TARGET_URL": "/x/",
        "POLICY": {"KIND": "time_bucket", "ADMIT_PER_SECOND": 1},
    },
)
def test_errors_on_short_secret() -> None:
    issues: Any = check_waiting_room_settings(app_configs=None)
    assert "waiting_room.E002" in _ids(issues)


@override_settings(
    WAITING_ROOM={
        "SECRET_KEY": "x" * 64,
        "TARGET_URL": "x/no-leading-slash",
        "POLICY": {"KIND": "time_bucket", "ADMIT_PER_SECOND": 1},
    },
)
def test_errors_on_relative_target_url() -> None:
    issues: Any = check_waiting_room_settings(app_configs=None)
    assert "waiting_room.E003" in _ids(issues)


@override_settings(
    WAITING_ROOM={
        "SECRET_KEY": "x" * 64,
        "TARGET_URL": "/checkout/",
        "POLICY": {"KIND": "time_bucket", "ADMIT_PER_SECOND": 1},
        "PROTECT": [("/checkout/", "default")],
    },
    MIDDLEWARE=[],  # missing the waiting room middleware
)
def test_warns_when_protect_set_without_middleware() -> None:
    issues: Any = check_waiting_room_settings(app_configs=None)
    assert "waiting_room.W003" in _ids(issues)


@override_settings(
    WAITING_ROOM={
        "SECRET_KEY": "x" * 64,
        "TARGET_URL": "/checkout/",
        "POLICY": {"KIND": "time_bucket", "ADMIT_PER_SECOND": 1},
    },
)
def test_clean_when_well_configured() -> None:
    issues: Any = check_waiting_room_settings(app_configs=None)
    assert all(isinstance(i, Warning) for i in issues)
    assert "waiting_room.E001" not in _ids(issues)
    assert "waiting_room.E002" not in _ids(issues)
    assert "waiting_room.E003" not in _ids(issues)


@pytest.mark.django_db
def test_check_command_runs_clean() -> None:
    """Belt-and-braces: invoke `manage.py check` to confirm no Errors come out."""
    from io import StringIO

    from django.core.management import call_command

    buf = StringIO()
    call_command("check", stdout=buf, stderr=buf)
    out = buf.getvalue()
    assert "Errors" not in out or "0 errors" in out
