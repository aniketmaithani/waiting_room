"""Test settings module — used by pytest-django via DJANGO_SETTINGS_MODULE."""

from __future__ import annotations

DEBUG = False
ALLOWED_HOSTS = ["*"]
SECRET_KEY = "x" * 64

DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
}

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.admin",
    "django.contrib.messages",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "waiting_room.adapters.django",
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "waiting_room.adapters.django.middleware.WaitingRoomMiddleware",
]

ROOT_URLCONF = "waiting_room.tests._urls"

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

STATIC_URL = "/static/"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

WAITING_ROOM = {
    "SECRET_KEY": "x" * 64,
    "TARGET_URL": "/checkout/",
    "POLICY": {"KIND": "time_bucket", "ADMIT_PER_SECOND": 100, "BURST": 100},
    "CAPACITY": 10,
    "PROTECT": [("/checkout/", "default")],
    "RATE_LIMIT_PER_IP_PER_MINUTE": 100_000,
    "AUDIT_LOG": True,
    "COOKIE_SECURE": False,
}
