"""Settings for the waiting_room example shop.

A flash sale: anyone can browse ``/``, but ``/checkout/`` sits behind a
waiting room that lets people in slowly (1 per second by default, at most 5
checking out at once) so the queue is easy to watch. Every knob can be
overridden with an environment variable; see the README's Example section.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# Development-only defaults. Never reuse these outside the example.
SECRET_KEY = _env("DJANGO_SECRET_KEY", "example-shop-insecure-django-secret-key")
DEBUG = _env("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "waiting_room.adapters.django",
    "shop",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # After auth, so ALLOWLIST_USER_IDS can see request.user.
    "waiting_room.adapters.django.middleware.WaitingRoomMiddleware",
]

ROOT_URLCONF = "shop.urls"
WSGI_APPLICATION = "shop.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": _env("SHOP_DB", str(BASE_DIR / "db.sqlite3")),
    },
}

STATIC_URL = "static/"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

WAITING_ROOM = {
    "SECRET_KEY": _env("WAITING_ROOM_SECRET", "example-shop-insecure-waiting-room-secret"),
    "TARGET_URL": "/checkout/",
    # Slow on purpose so you can watch the queue move.
    "POLICY": {
        "KIND": "time_bucket",
        "ADMIT_PER_SECOND": float(_env("ADMIT_PER_SECOND", "1")),
        "BURST": 1,
    },
    "CAPACITY": int(_env("CAPACITY", "5")),
    "ADMITTED_SESSION_TTL_SECONDS": int(_env("ADMITTED_SESSION_TTL_SECONDS", "120")),
    "REDIS": {"URL": _env("REDIS_URL", "redis://localhost:6379/0"), "KEY_PREFIX": "wr-shop"},
    "PROTECT": [("/checkout/", "default")],
    # The crowd simulator sends every shopper from 127.0.0.1, so allow plenty.
    "RATE_LIMIT_PER_IP_PER_MINUTE": int(_env("RATE_LIMIT_PER_IP_PER_MINUTE", "6000")),
    # Secure cookies need HTTPS; the example serves plain HTTP when DEBUG is on.
    "COOKIE_SECURE": _env("COOKIE_SECURE", "0" if DEBUG else "1") == "1",
    "AUDIT_LOG": True,
}
