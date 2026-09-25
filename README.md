# waiting_room

A plug-and-play virtual waiting room for high-traffic Python web apps. Drop it
in front of any endpoint that buckles under thundering-herd traffic (flash
sales, ticket drops, limited-inventory checkout) and it will:

1. Hold users in a Redis-backed queue with a live position display.
2. Admit them at a controlled rate (time-bucket, capacity-aware, or both).
3. Mint a signed, single-use, fingerprint-bound token and let them through.

Built around a framework-agnostic core, with a polished **Django** adapter
shipped today (FastAPI / Flask roadmapped).

> **Status:** alpha. Core engine + Django adapter: AppConfig, middleware,
> decorator, views, admin, management commands, system checks and migrations.
> The test suite runs against fakeredis or a real Redis, and `mypy --strict`
> passes.

---

## How it works

1. A request hits a protected path without a pass → the caller is enqueued
   (idempotently, keyed by a `wr_session` cookie) and redirected to
   `/_waiting-room/?room=…&sid=…&next=…`.
2. The waiting page polls `/_waiting-room/status`. Every poll also drives an
   **admission tick**: one Lua script that frees expired slots and admits
   people from the front of the queue within the room's rate **and**
   capacity, atomically, across all worker processes.
3. Once admitted, the page POSTs to `/_waiting-room/admit`, which mints a
   **single-use, fingerprint-bound ticket** (HMAC-SHA256) as a cookie and
   sends the browser back to the page it originally asked for.
4. On that request the gate redeems the ticket exactly once and swaps it for a
   **pass** cookie, which is valid for `ADMITTED_SESSION_TTL_SECONDS` and
   bound to the same fingerprint. Every later request is checked with a pure
   HMAC verify, with no Redis round trip.
5. The admitted user's capacity slot is held for the pass lifetime, or until
   you call `release_admission()` (e.g. once checkout completes).

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
    # Admit 100/s (bursts of up to 100), never more than 5,000 at once.
    "POLICY":  {"KIND": "time_bucket", "ADMIT_PER_SECOND": 100, "BURST": 100},
    "CAPACITY": 5_000,                # 0 = rate only
    "REDIS":   {"URL": env("REDIS_URL"), "KEY_PREFIX": "wr"},

    # How long an admitted user keeps access (and their capacity slot).
    "ADMITTED_SESSION_TTL_SECONDS": 900,
    # Time an admitted user has to arrive before their slot goes to the next person.
    "ADMISSION_GRACE_SECONDS": 60,

    # Number of reverse proxies that append to X-Forwarded-For. 0 (default)
    # ignores the header, since clients can forge it.
    "TRUSTED_PROXY_COUNT": 1,

    # Routes that should be gated by a waiting room. Tuple of (prefix, room_name).
    "PROTECT": [("/checkout/", "default")],

    # VIPs that bypass the queue (IPs or CIDR networks).
    "ALLOWLIST_IPS": ["10.0.0.5", "192.168.0.0/16"],
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

That's it. Hit `/checkout/` without a pass and you'll be redirected to a
waiting page that polls for its position and sends you through when it's your
turn.

### 7. Release slots when the flow is done (recommended)

```python
from waiting_room.adapters.django import release_admission

def order_complete(request):
    response = render(request, "thanks.html")
    release_admission(request, response, "default")   # next person gets in sooner
    return response
```

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
            "POLICY": {"KIND": "capacity_aware"},   # uses the room's CAPACITY
            "CAPACITY": 500,
        },
    },
    "PROTECT": [("/checkout/", "checkout"), ("/tickets/", "tickets")],
}
```

---

## Admin

`/admin/waiting_room/` ships with two pages:

- **Admission events**: read-only audit log (filterable by room / event type / date)
- **Room status**: live queue + admitted counts per room with a kill-switch
  toggle. Toggling requires the `waiting_room.change_roomstatus` permission.

---

## Management commands

| Command | What it does |
| --- | --- |
| `python manage.py waiting_room_status` | Print queue size / admitted / kill-switch state for every room |
| `python manage.py waiting_room_kill_switch <room> on\|off` | Engage / release the kill switch for a room |
| `python manage.py waiting_room_reclaim [--room <name>]` | Sweep abandoned/expired sessions back to the free pool |
| `python manage.py waiting_room_flush <room> --yes` | Drop all queue and admitted state for a room (incident recovery) |

Expired admission slots are freed automatically on every admission tick.
Scheduling `waiting_room_reclaim` from cron / Celery Beat is optional
housekeeping: it also drops waiters who stopped polling for
`QUEUED_SESSION_TTL_SECONDS`.

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
│   ├── strategies.py    # optional extra per-process caps
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
│   ├── _gate.py         # admission gate shared by middleware + decorator
│   ├── middleware.py
│   ├── decorators.py
│   ├── release.py       # release_admission() helper
│   ├── views.py         # waiting page, status, SSE stream, admit, health
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

- **Atomic admissions**: the rate (shared token bucket), the capacity check,
  freeing expired slots and popping the queue all happen in one Lua script,
  so any number of workers together never exceed the configured rate or
  capacity, or admit the same session twice.
- **Waiters keep their place**: active waiters are kept alive by their polls;
  only sessions that stop polling are reclaimed.
- **Cluster-safe** — every per-room key is wrapped in `{room}` so Redis
  Cluster pins them to one slot.
- **Plug-and-play** — `INSTALLED_APPS += ["waiting_room.adapters.django"]` +
  one `MIDDLEWARE` entry + `migrate`. No third-party dependencies beyond Redis.
- **Pluggable** — every component (`StorageBackend`, `AdmissionStrategy`,
  `TokenSigner`, `RateLimiter`, `EventEmitter`, `MetricsRecorder`) is an ABC.
- **Observable** — Prometheus + structlog are soft deps; no-op shims if absent.
- **Failure modes** — `FAIL_CLOSED` (default) or `FAIL_OPEN`. Selected via the
  `FAILURE_MODE` setting.
- **Security**: HMAC-SHA256 tokens scoped by purpose (single-use ticket vs.
  reusable pass) and room, bound to an IP + UA fingerprint, with a Redis nonce
  store for single-use enforcement. `next` redirects are restricted to
  same-site paths. `X-Forwarded-For` is only trusted behind configured
  proxies. Session ids can't be taken over by another client. Enqueues are
  rate limited per IP (HTTP 429).
- **Scales with WSGI**: the waiting page polls with jittered backoff. The
  SSE endpoint (`/_waiting-room/position`) is opt-in for ASGI deployments and
  caps each connection at 55s.

---

## Develop

```bash
uv pip install -e ".[dev]"
pytest                                   # fakeredis
WAITING_ROOM_TEST_REDIS_URL=redis://localhost:6379/15 pytest   # real Redis (DB is flushed!)
ruff check waiting_room/ && ruff format --check waiting_room/
mypy waiting_room/
```

---

## Roadmap

- FastAPI + Flask adapters
- Helm chart + docker-compose for Redis Sentinel/Cluster
- Locust load test (50K concurrent simulated)
- OpenTelemetry tracing hooks
- DynamoDB storage backend
