"""Django system checks — surface misconfiguration via ``manage.py check``.

Registered at module import time (which happens from ``apps.py``'s ``ready``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.conf import settings
from django.core.checks import Error, Warning, register

_MIDDLEWARE_PATH = "waiting_room.adapters.django.middleware.WaitingRoomMiddleware"
_DECORATOR_PATH = "waiting_room.adapters.django.decorators.waiting_room_protect"


@register()
def check_waiting_room_settings(
    app_configs: Any,
    **_: Any,
) -> Sequence[Error | Warning]:
    issues: list[Error | Warning] = []
    raw = getattr(settings, "WAITING_ROOM", None)

    if raw is None:
        issues.append(
            Warning(
                "WAITING_ROOM setting is not configured.",
                hint=(
                    "Add a WAITING_ROOM dict to settings with at least "
                    "SECRET_KEY and TARGET_URL. See the README for the full schema."
                ),
                id="waiting_room.W001",
            ),
        )
        return issues

    rooms = _rooms_from_setting(raw)
    if not rooms:
        issues.append(
            Error(
                "WAITING_ROOM is set but contains no rooms.",
                hint="Provide a single config dict or set WAITING_ROOM['ROOMS'].",
                id="waiting_room.E001",
            ),
        )
        return issues

    for name, cfg in rooms.items():
        secret = str(cfg.get("SECRET_KEY", ""))
        if len(secret) < 32:
            issues.append(
                Error(
                    f"WAITING_ROOM[{name!r}] SECRET_KEY must be at least 32 chars.",
                    hint=(
                        "Generate one with "
                        "`python -c 'import secrets; print(secrets.token_urlsafe(48))'`."
                    ),
                    id="waiting_room.E002",
                ),
            )
        target = str(cfg.get("TARGET_URL", ""))
        if not target.startswith("/"):
            issues.append(
                Error(
                    f"WAITING_ROOM[{name!r}] TARGET_URL must be an absolute path.",
                    id="waiting_room.E003",
                ),
            )
        redis_url = (cfg.get("REDIS") or {}).get("URL") or getattr(
            settings,
            "REDIS_URL",
            None,
        )
        if not redis_url:
            issues.append(
                Warning(
                    f"WAITING_ROOM[{name!r}] has no REDIS.URL; falling back to "
                    "redis://localhost:6379/0. Set one in production.",
                    id="waiting_room.W002",
                ),
            )

    middleware = getattr(settings, "MIDDLEWARE", []) or []
    protect = (raw.get("PROTECT") or []) if isinstance(raw, dict) else []
    if protect and _MIDDLEWARE_PATH not in middleware:
        issues.append(
            Warning(
                f"WAITING_ROOM['PROTECT'] is set but {_MIDDLEWARE_PATH} is missing "
                "from MIDDLEWARE — middleware-based protection won't run.",
                hint=(
                    f"Add {_MIDDLEWARE_PATH!r} to MIDDLEWARE, "
                    "or use the @waiting_room_protect decorator."
                ),
                id="waiting_room.W003",
            ),
        )

    return issues


def _rooms_from_setting(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    rooms = raw.get("ROOMS")
    if isinstance(rooms, dict):
        return {str(k): dict(v) for k, v in rooms.items()}
    # Single-room shorthand.
    name = str(raw.get("NAME", "default"))
    return {name: dict(raw)}
