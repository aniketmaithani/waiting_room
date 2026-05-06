# waiting_room

A plug-and-play virtual waiting room for high-traffic Python web apps. Drop it
in front of any endpoint that buckles under thundering-herd traffic (flash
sales, ticket drops, limited-inventory checkout) and it will:

1. Hold users in a Redis-backed queue with a live position display.
2. Admit them at a controlled rate (time-bucket, capacity-aware, or both).
3. Mint a signed, single-use, fingerprint-bound token and let them through.

Built around a framework-agnostic core, with a polished **Django** adapter
shipped today (FastAPI / Flask roadmapped).

> **Status:** alpha. 55 tests passing. Core engine + Django adapter complete:
> AppConfig, middleware, decorator, views, SSE, admin, management commands,
> system checks, and a real migration.

---

## Quickstart — Django

### 1. Install

```bash
pip install "waiting_room[django]"
```

### 2. Add to `INSTALLED_APPS` and `MIDDLEWARE`

```python
# settings.py
INSTALLED_APPS = [
    # ...
    "waiting_room.adapters.django",
]

MIDDLEWARE = [
    # security middleware first
    "django.middleware.security.SecurityMiddleware",
    # ...
    "waiting_room.adapters.django.middleware.WaitingRoomMiddleware",
    # ... your other middleware
]
```

### 3. Configure

```python
# settings.py
WAITING_ROOM = {
    "SECRET_KEY": env("WAITING_ROOM_SECRET"),       # >= 32 chars
    "TARGET_URL": "/checkout/",
    "POLICY":  {"KIND": "time_bucket", "ADMIT_PER_SECOND": 100, "BURST": 100},
    "CAPACITY": 5_000,
    "REDIS":   {"URL": env("REDIS_URL"), "KEY_PREFIX": "wr"},

    # Routes that should be gated by a waiting room. Tuple of (prefix, room_name).
    "PROTECT": [("/checkout/", "default")],

    # VIPs that bypass the queue.
    "ALLOWLIST_IPS": ["10.0.0.5"],
    "ALLOWLIST_USER_IDS": ["1", "42"],

    # When True, lifecycle events are persisted to AdmissionEvent.
    "AUDIT_LOG": True,

    # FAIL_CLOSED (default) shows the waiting page when Redis is down.
    # FAIL_OPEN lets traffic through.
    "FAILURE_MODE": "fail_closed",
}
```

### 4. Wire URLs

```python
# urls.py
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("waiting_room.adapters.django.urls")),  # waiting page + APIs
    path("checkout/", checkout_view),                          # protected
]
```

### 5. Run migrations

```bash
python manage.py migrate waiting_room
```

That creates the `waiting_room_admissionevent` audit-log table.

### 6. Verify

```bash
python manage.py check                   # warns about misconfiguration
python manage.py waiting_room_status     # live queue / admitted counts
```

That's it. Hit `/checkout/` with no token and you'll be redirected to a
waiting page that updates over Server-Sent Events.

---

## Decorator alternative

Skip the middleware and gate one view at a time:

```python
from waiting_room.adapters.django import waiting_room_protect

@waiting_room_protect("default")
def checkout_view(request):
    ...
```

---

## Multiple rooms

```python
WAITING_ROOM = {
    "ROOMS": {
        "checkout": {
            "SECRET_KEY": env("WR_CHECKOUT_SECRET"),
            "TARGET_URL": "/checkout/",
            "POLICY": {"KIND": "time_bucket", "ADMIT_PER_SECOND": 50},
            "CAPACITY": 1000,
        },
        "tickets": {
            "SECRET_KEY": env("WR_TICKETS_SECRET"),
            "TARGET_URL": "/tickets/buy/",
            "POLICY": {"KIND": "capacity_aware"},
            "CAPACITY": 500,
        },
    },
    "PROTECT": [("/checkout/", "checkout"), ("/tickets/", "tickets")],
}
```

---

## Admin

`/admin/waiting_room/` ships with two pages:

- **Admission events** — read-only audit log (filterable by room / event type / date)
- **Room status** — live queue + admitted counts per room with a kill-switch toggle button

---

## Management commands

| Command | What it does |
| --- | --- |
| `python manage.py waiting_room_status` | Print queue size / admitted / kill-switch state for every room |
| `python manage.py waiting_room_kill_switch <room> on\|off` | Engage / release the kill switch for a room |
| `python manage.py waiting_room_reclaim [--room <name>]` | Sweep abandoned/expired sessions back to the free pool |
| `python manage.py waiting_room_flush <room> --yes` | Drop all queue and admitted state for a room (incident recovery) |

Schedule `waiting_room_reclaim` from cron / Celery Beat to keep abandoned slots from leaking.

---

## System checks (`manage.py check`)

| ID | Severity | Trigger |
| --- | --- | --- |
| `waiting_room.W001` | Warning | `WAITING_ROOM` setting missing |
| `waiting_room.E001` | Error | `WAITING_ROOM` empty |
| `waiting_room.E002` | Error | A room's `SECRET_KEY` is shorter than 32 chars |
| `waiting_room.E003` | Error | A room's `TARGET_URL` is not absolute |
| `waiting_room.W002` | Warning | A room has no `REDIS.URL` and no `REDIS_URL` global |
| `waiting_room.W003` | Warning | `PROTECT` is set but the middleware isn't installed |

---

## Lifecycle hooks

```python
from django.dispatch import receiver
from waiting_room.adapters.django import waiting_room_event

@receiver(waiting_room_event)
def on_event(sender, event_type, payload, **_):
    if event_type == "admitted":
        push_to_analytics(payload["session_id"])
```

---

## What's in the box

```
waiting_room/
├── core/                # framework-agnostic engine
│   ├── engine.py        # WaitingRoom facade
│   ├── tokens.py        # HMAC-SHA256 admission tokens (single-use, fp-bound)
│   ├── strategies.py    # TimeBucket, CapacityAware, Composite
│   ├── storage/         # Redis backend (cluster-safe via {room} hash tag)
│   ├── lua/             # atomic Lua scripts (enqueue, admit_batch, reclaim, ...)
│   ├── ratelimit.py
│   ├── events.py
│   ├── metrics.py       # optional Prometheus
│   └── settings.py
├── adapters/django/     # Django plug-and-play app
│   ├── apps.py          # AppConfig (auto-builds rooms on startup)
│   ├── conf.py          # reads settings.WAITING_ROOM
│   ├── checks.py        # manage.py check integration
│   ├── middleware.py
│   ├── decorators.py
│   ├── views.py         # waiting page, SSE stream, status, admit, health
│   ├── models.py        # AdmissionEvent (audit log)
│   ├── migrations/
│   ├── admin.py         # AdmissionEvent + Room status with kill switch
│   ├── signals.py       # waiting_room_event Signal + audit handler
│   ├── management/commands/   # waiting_room_status / kill_switch / reclaim / flush
│   ├── templates/waiting_room/    # waiting.html, killswitch.html, admin_room_ops.html
│   └── urls.py
└── tests/               # pytest + pytest-django, fakeredis-backed
```

---

## Non-functional guarantees

- **Atomic admissions** — ZRANGE+ZREM+ZADD+HSET happen in a single Lua script;
  two workers never admit the same session twice.
- **Cluster-safe** — every per-room key is wrapped in `{room}` so Redis
  Cluster pins them to one slot.
- **Plug-and-play** — `INSTALLED_APPS += ["waiting_room.adapters.django"]` +
  one `MIDDLEWARE` entry + `migrate`. No third-party dependencies beyond Redis.
- **Pluggable** — every component (`StorageBackend`, `AdmissionStrategy`,
  `TokenSigner`, `RateLimiter`, `EventEmitter`, `MetricsRecorder`) is an ABC.
- **Observable** — Prometheus + structlog are soft deps; no-op shims if absent.
- **Failure modes** — `FAIL_CLOSED` (default) or `FAIL_OPEN`. Selected via the
  `FAILURE_MODE` setting.
- **Security** — HMAC-SHA256 signing, payload fingerprint binding (IP + UA
  hash), Redis-backed nonce store for single-use enforcement, URL-safe tokens
  capped at 256 chars.

---

## Develop

```bash
uv pip install -e ".[dev]"
pytest                 # 55 tests
ruff check waiting_room/
```

---

## Roadmap

- FastAPI + Flask adapters
- Helm chart + docker-compose for Redis Sentinel/Cluster
- Locust load test (50K concurrent simulated)
- OpenTelemetry tracing hooks
- DynamoDB storage backend
