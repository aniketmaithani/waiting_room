# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While
the version is `0.x`, minor releases may contain breaking changes; they are
called out under **Changed** or **Removed**.

## [Unreleased]

## [0.1.0] - 2026-09-25

First public release: a plug-and-play virtual waiting room for Python web
apps, with a framework-agnostic core and a Django adapter.

### Added

#### Core engine (`waiting_room`)
- `WaitingRoom` facade: `enqueue`, `position`, `status_payload`, `try_admit`,
  `redeem`, `verify_pass`, `release`, `abandon`, `reclaim`, `stats`, `flush`,
  kill switch and health check.
- Redis-backed queue (`RedisStorageBackend`) with every multi-step mutation in
  a Lua script (`enqueue`, `admit_batch`, `position`, `reclaim`, `release`).
  All of a room's keys share a `{room}` hash tag, so it is Redis Cluster-safe.
- Admission policies: `AdmissionPolicy.time_bucket(admit_per_second, burst=)`
  and `AdmissionPolicy.capacity_aware(capacity)`. A time bucket combined with
  `WaitingRoomConfig.capacity` gives "both": a steady rate that never exceeds
  downstream capacity.
- Rate and capacity are enforced atomically in Redis on every admission tick,
  so they hold across any number of worker processes. Expired admission slots
  are freed on the same tick, so no cron job is required.
- Admission via a single-use, fingerprint-bound HMAC-SHA256 **ticket**,
  exchanged on first use for a reusable **pass**
  (`admitted_session_ttl_seconds`). Passes are checked with HMAC only, with no
  Redis round trip on the hot path.
- `hold_admission` keeps a capacity slot for the pass lifetime.
  `release()` frees it early.
- Waiters stay in line while they keep polling: last-seen tracking plus a
  session TTL refresh on every poll. Only idle sessions are reclaimed.
- Status polls drive admission ticks (throttled per process) and report a
  `closed` flag while the kill switch is engaged.
- Per-IP enqueue rate limiting (`RateLimitedError`), with a fixed window scoped
  per room.
- IP and CIDR allowlists (`allowlist_ips`) and user-id allowlists.
- Configurable `FailureMode.FAIL_CLOSED` (default) / `FAIL_OPEN`.
- Pluggable ABCs: `StorageBackend`, `TokenSigner`, `AdmissionStrategy`,
  `RateLimiter`, `EventEmitter`, `MetricsRecorder`, `SessionStore`.
- Lifecycle events (`enqueued`, `admitted`, `redeemed`, `released`, `expired`,
  `rejected`, `kill_switch_toggled`) with an in-process emitter.
- Optional Prometheus metrics (`metrics` extra), with collectors shared safely
  across rooms.

#### Django adapter (`waiting_room.adapters.django`)
- `WaitingRoomMiddleware` protecting URL prefixes from `WAITING_ROOM["PROTECT"]`,
  and a `@waiting_room_protect("room")` view decorator, both built on one shared
  admission gate.
- Views: waiting page, JSON status polling, admit callback, health, and an
  opt-in SSE stream (connections capped at 55s).
- A waiting page that polls with jittered exponential backoff and returns the
  user to the page they originally requested.
- `release_admission(request, response, room)` helper to free a slot once the
  protected flow completes.
- Settings: single-room shorthand or `ROOMS`, `POLICY`, `CAPACITY`,
  `ADMISSION_GRACE_SECONDS`, `ADMITTED_SESSION_TTL_SECONDS`,
  `QUEUED_SESSION_TTL_SECONDS`, `TRUSTED_PROXY_COUNT`, `ALLOWLIST_IPS`,
  `ALLOWLIST_USER_IDS`, `FAILURE_MODE`, `AUDIT_LOG`, `REDIS`, cookie options.
- `AdmissionEvent` audit-log model (opt-in via `AUDIT_LOG`) and the
  `waiting_room_event` signal.
- Admin: read-only audit log, plus a live "Room status" page with a kill-switch
  toggle (POST only, requires `waiting_room.change_roomstatus`).
- Management commands: `waiting_room_status`, `waiting_room_kill_switch`,
  `waiting_room_reclaim`, `waiting_room_flush --yes`.
- System checks `waiting_room.W001`–`W003` and `E001`–`E003`.
- Migrations `0001_initial` and `0002_roomstatus`.

#### Project
- Runnable example: `examples/django_shop/`, a flash-sale shop with a
  standard-library crowd simulator (`crowd.py`), exercised end to end by the
  test suite.
- Test suite (pytest + pytest-django) that runs on fakeredis or, with
  `WAITING_ROOM_TEST_REDIS_URL`, on a real Redis.
- `ruff` (including bandit rules) and `mypy --strict` clean.

### Security
- Tokens are scoped by purpose (ticket vs. pass) and by room, bound to an IP +
  user-agent fingerprint, and single-use tickets are enforced with a Redis
  nonce store.
- `X-Forwarded-For` is ignored unless `TRUSTED_PROXY_COUNT` is set, and is then
  read from the correct hop, so clients cannot spoof allowlisted IPs.
- `next` redirects are limited to same-site paths (no open redirects or
  `javascript:` URLs).
- A session id cannot be taken over by a different client fingerprint, and
  malformed session ids are ignored.
- The admit callback only serves the browser holding the session cookie.
- Cookies are `HttpOnly`, `SameSite=Lax` by default, and `Secure` outside
  `DEBUG`.

[Unreleased]: https://github.com/aniketmaithani/waiting_room/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/aniketmaithani/waiting_room/releases/tag/v0.1.0
