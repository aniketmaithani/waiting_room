"""High-level facade.

Wires storage, strategy, token signer, rate limiter, events, and metrics into
a single object that adapters call. ``WaitingRoom`` is the only class an
adapter needs to know about — every other piece is reachable through it or
through the ``WaitingRoomConfig`` passed in.
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import threading
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING

from waiting_room.core._types import (
    AdmissionLimits,
    AdmissionTicket,
    EventType,
    Fingerprint,
    QueuePosition,
    Session,
    SessionState,
    is_valid_session_id,
)
from waiting_room.core.events import InProcessEventEmitter
from waiting_room.core.exceptions import (
    BackendUnavailableError,
    KillSwitchEngagedError,
    SessionNotFoundError,
)
from waiting_room.core.interfaces import (
    AdmissionStrategy,
    EventEmitter,
    MetricsRecorder,
    RateLimiter,
    StorageBackend,
    TokenSigner,
)
from waiting_room.core.metrics import default_metrics
from waiting_room.core.ratelimit import NoopRateLimiter, RedisRateLimiter
from waiting_room.core.settings import (
    FailureMode,
    WaitingRoomConfig,
    _PolicyKind,
)
from waiting_room.core.storage.redis_backend import RedisStorageBackend
from waiting_room.core.tokens import HMACTokenSigner

if TYPE_CHECKING:
    import redis as redis_pkg

_log = logging.getLogger("waiting_room.engine")

_KILL_SWITCH = -1
_FOREIGN_SESSION = -2

_MAX_BATCH = 1_000
"""Upper bound on sessions admitted per tick, keeping each Lua call short."""


def _ua_hash(user_agent: str) -> str:
    return hashlib.sha256(user_agent.encode("utf-8")).hexdigest()[:16]


def _admission_limits(config: WaitingRoomConfig) -> AdmissionLimits:
    """Translate the configured policy into limits the storage layer enforces.

    ``time_bucket`` admits at a steady rate *and* respects ``config.capacity``
    (set it to ``0`` for rate-only). ``capacity_aware`` admits whenever a slot
    is free, using the policy's capacity.
    """
    policy = config.policy
    if policy.kind is _PolicyKind.CAPACITY_AWARE:
        return AdmissionLimits(
            capacity=policy.capacity,
            grace_seconds=config.admission_grace_seconds,
        )
    return AdmissionLimits(
        capacity=config.capacity,
        rate_per_second=policy.admit_per_second,
        burst=policy.burst,
        grace_seconds=config.admission_grace_seconds,
    )


def _build_redis_client(url: str, **kwargs: object) -> redis_pkg.Redis:
    import redis as redis_pkg  # local import to keep redis a soft dep at parse time

    return redis_pkg.from_url(url, decode_responses=False, **kwargs)


class WaitingRoom:
    """Facade over the queue. One instance per logical room (per process is fine)."""

    def __init__(
        self,
        config: WaitingRoomConfig,
        *,
        storage: StorageBackend | None = None,
        strategy: AdmissionStrategy | None = None,
        token_signer: TokenSigner | None = None,
        rate_limiter: RateLimiter | None = None,
        emitter: EventEmitter | None = None,
        metrics: MetricsRecorder | None = None,
        redis_client: redis_pkg.Redis | None = None,
    ) -> None:
        self.config = config

        # Allow tests/integrators to inject a pre-built Redis client; otherwise build one.
        client = redis_client
        if client is None and (storage is None or rate_limiter is None or token_signer is None):
            client = _build_redis_client(
                config.storage.url,
                socket_timeout=config.storage.socket_timeout,
                socket_connect_timeout=config.storage.socket_connect_timeout,
                health_check_interval=config.storage.health_check_interval,
                max_connections=config.storage.max_connections,
            )
        self._redis = client

        self._storage: StorageBackend = storage or RedisStorageBackend(
            client,  # type: ignore[arg-type]
            key_prefix=config.storage.key_prefix,
        )
        # Built-in limits are enforced atomically by the storage backend. An
        # injected strategy can only narrow them further, never widen them.
        self._limits = _admission_limits(config)
        self._strategy = strategy
        self._signer: TokenSigner = token_signer or HMACTokenSigner(
            config.secret_key,
            redis_client=client,
            key_prefix=f"{config.storage.key_prefix}:{{{config.name}}}:nonce",
            bind_fingerprint=config.bind_fingerprint,
        )
        self._rate_limiter: RateLimiter = rate_limiter or (
            RedisRateLimiter(
                client,  # type: ignore[arg-type]
                limit=config.rate_limit_per_ip_per_minute,
                window_seconds=60,
                key_prefix=f"{config.storage.key_prefix}:rl",
            )
            if client is not None
            else NoopRateLimiter()
        )
        self._emitter: EventEmitter = emitter or InProcessEventEmitter()
        self._metrics: MetricsRecorder = metrics or default_metrics()
        # Per-instance tiebreaker so rapid-fire enqueues never collide on score.
        self._seq = itertools.count()
        self._seq_lock = threading.Lock()

    # ---- public API ----------------------------------------------------------

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def emitter(self) -> EventEmitter:
        return self._emitter

    @property
    def effective_capacity(self) -> int:
        """Concurrent admission cap actually enforced (``0`` means unlimited)."""
        return self._limits.capacity

    def is_allowlisted(self, *, ip: str, user_id: str | None) -> bool:
        if ip and ip in self.config.allowlist_ips:
            return True
        return bool(user_id and user_id in self.config.allowlist_user_ids)

    def enqueue(
        self,
        *,
        ip: str,
        user_agent: str,
        user_id: str | None = None,
        existing_session_id: str | None = None,
    ) -> tuple[Session, QueuePosition]:
        """Place a caller in the queue. Idempotent for an existing session id."""
        if not self._rate_limiter.acquire(f"enqueue:{ip}"):
            self._metric_incr("enqueue_rate_limited")
            self._emitter.emit(EventType.REJECTED, {"reason": "rate_limited", "ip": ip})
            msg = "rate limited"
            raise BackendUnavailableError(msg)

        fp = Fingerprint(ip=ip, user_agent_hash=_ua_hash(user_agent))
        if not is_valid_session_id(existing_session_id):
            existing_session_id = None
        session = Session(
            session_id=existing_session_id or Session().session_id,
            room=self.config.name,
            fingerprint=fp,
            user_id=user_id,
            state=SessionState.QUEUED,
        )

        try:
            position = self._store_session(session)
            if position == _FOREIGN_SESSION:
                # The id belongs to another client: never hand over their place.
                session.session_id = Session().session_id
                position = self._store_session(session)
        except BackendUnavailableError:
            return self._handle_backend_unavailable(session)

        if position == _KILL_SWITCH:
            self._emitter.emit(EventType.REJECTED, {"reason": "kill_switch", "ip": ip})
            self._metric_incr("enqueue_rejected_killswitch")
            raise KillSwitchEngagedError
        if position == 0:
            session.state = SessionState.ADMITTED
            return session, QueuePosition(position=0, queue_size=0, estimated_wait_seconds=0.0)

        size = self._safe_queue_size()
        wait = self._estimate_wait(position)
        snapshot = QueuePosition(position=position, queue_size=size, estimated_wait_seconds=wait)
        self._emitter.emit(
            EventType.ENQUEUED,
            {"session_id": session.session_id, "position": position, "size": size},
        )
        self._metric_incr("enqueued")
        self._metric_gauge("queue_size", float(size))
        return session, snapshot

    def position(self, session_id: str) -> QueuePosition:
        try:
            snap = self._storage.position(
                self.config.name,
                session_id,
                ttl_seconds=self.config.queued_session_ttl_seconds,
            )
        except BackendUnavailableError:
            if self.config.failure_mode is FailureMode.FAIL_OPEN:
                return QueuePosition(position=0, queue_size=0, estimated_wait_seconds=0.0)
            raise
        wait = self._estimate_wait(snap.position) if snap.position > 0 else 0.0
        return QueuePosition(
            position=snap.position,
            queue_size=snap.queue_size,
            estimated_wait_seconds=wait,
        )

    def try_admit(self, session_id: str) -> AdmissionTicket | None:
        """Run an admission tick and, if this session was admitted, mint a ticket.

        Adapters call this when a queued client polls. Returns ``None`` if the
        session is still waiting.
        """
        try:
            self._tick_admit()
            session = self._storage.get_session(self.config.name, session_id)
        except BackendUnavailableError:
            if self.config.failure_mode is FailureMode.FAIL_OPEN:
                return self._mint_open_ticket(session_id)
            raise
        if session is None:
            raise SessionNotFoundError(session_id)
        if session.state is not SessionState.ADMITTED:
            return None
        ticket = self._signer.issue(
            session_id=session_id,
            room=self.config.name,
            fingerprint=session.fingerprint.as_str() if session.fingerprint else "",
            ttl_seconds=self.config.token_ttl_seconds,
        )
        self._emitter.emit(
            EventType.ADMITTED,
            {"session_id": session_id, "expires_at": ticket.expires_at},
        )
        self._metric_incr("admitted")
        return ticket

    def redeem(self, token: str, *, ip: str, user_agent: str) -> AdmissionTicket:
        """Validate and mark a token as used. Raises ``InvalidTokenError`` on failure."""
        fp = Fingerprint(ip=ip, user_agent_hash=_ua_hash(user_agent)).as_str()
        ticket = self._signer.verify(token, fingerprint=fp)
        # Single-use: claim the nonce. Raises if already used.
        self._signer.mark_used(token)
        self._emitter.emit(EventType.REDEEMED, {"session_id": ticket.session_id})
        self._metric_incr("redeemed")
        return ticket

    def release(self, session_id: str) -> bool:
        """Free the admission slot held by this session (e.g. user finished)."""
        try:
            ok = self._storage.release_admission(self.config.name, session_id)
        except BackendUnavailableError:
            if self.config.failure_mode is FailureMode.FAIL_OPEN:
                return False
            raise
        if ok:
            self._emitter.emit(EventType.RELEASED, {"session_id": session_id})
            self._metric_incr("released")
        return ok

    def abandon(self, session_id: str) -> bool:
        """Caller left the page — remove them so the slot frees up."""
        try:
            return self._storage.remove(self.config.name, session_id)
        except BackendUnavailableError:
            if self.config.failure_mode is FailureMode.FAIL_OPEN:
                return False
            raise

    def reclaim(self) -> int:
        """Drop waiters idle for ``queued_session_ttl_seconds`` and expired admissions.

        Safe to call from a periodic task. Admission slots are also freed lazily
        on every admission tick, so this is housekeeping rather than a necessity.
        """
        cutoff = time.time() - self.config.queued_session_ttl_seconds
        try:
            count = self._storage.reclaim_expired(self.config.name, cutoff)
        except BackendUnavailableError:
            return 0
        if count:
            self._emitter.emit(EventType.EXPIRED, {"count": count})
            self._metric_incr("reclaimed", value=float(count))
        return count

    def set_kill_switch(self, *, engaged: bool) -> None:
        self._storage.set_kill_switch(self.config.name, engaged=engaged)
        self._emitter.emit(EventType.KILL_SWITCH_TOGGLED, {"engaged": engaged})

    def is_kill_switch_engaged(self) -> bool:
        try:
            return self._storage.is_kill_switch_engaged(self.config.name)
        except BackendUnavailableError:
            # If we can't read the switch, behave per failure mode.
            return self.config.failure_mode is FailureMode.FAIL_CLOSED

    def healthcheck(self) -> bool:
        return self._storage.ping()

    # ---- internals -----------------------------------------------------------

    def _tick_admit(self) -> list[str]:
        max_n = _MAX_BATCH
        if self._strategy is not None:
            max_n = min(
                max_n,
                self._strategy.slots_available(
                    queue_size=self._safe_queue_size(),
                    admitted=self._safe_admitted_count(),
                    capacity=self._limits.capacity,
                ),
            )
            if max_n <= 0:
                return []
        return self._storage.admit_batch(self.config.name, max_n, limits=self._limits)

    def _safe_queue_size(self) -> int:
        try:
            return self._storage.queue_size(self.config.name)
        except BackendUnavailableError:
            return 0

    def _safe_admitted_count(self) -> int:
        try:
            return self._storage.admitted_count(self.config.name)
        except BackendUnavailableError:
            return 0

    def _estimate_wait(self, position: int) -> float | None:
        if position <= 0:
            return 0.0
        rate = self.config.policy.admit_per_second
        if rate <= 0:
            return None
        return max(0.0, position / rate)

    def _handle_backend_unavailable(
        self,
        session: Session,
    ) -> tuple[Session, QueuePosition]:
        _log.warning("waiting_room backend unavailable, applying %s", self.config.failure_mode)
        if self.config.failure_mode is FailureMode.FAIL_OPEN:
            session.state = SessionState.ADMITTED
            return session, QueuePosition(position=0, queue_size=0, estimated_wait_seconds=0.0)
        raise BackendUnavailableError("storage backend is unreachable")

    def _mint_open_ticket(self, session_id: str) -> AdmissionTicket:
        return self._signer.issue(
            session_id=session_id,
            room=self.config.name,
            fingerprint="",
            ttl_seconds=self.config.token_ttl_seconds,
        )

    def _store_session(self, session: Session) -> int:
        return self._storage.enqueue(
            self.config.name,
            session,
            score=self._next_score(session.enqueued_at),
            ttl_seconds=self.config.queued_session_ttl_seconds,
            check_fingerprint=self.config.bind_fingerprint,
        )

    def _next_score(self, base: float) -> float:
        """Return a strictly-increasing score so equal-timestamp enqueues stay ordered."""
        with self._seq_lock:
            n = next(self._seq)
        return base + n * 1e-9

    def _metric_incr(self, name: str, value: float = 1.0) -> None:
        self._metrics.incr(name, value, room=self.config.name)

    def _metric_gauge(self, name: str, value: float) -> None:
        self._metrics.gauge(name, value, room=self.config.name)

    # ---- adapter-friendly helpers --------------------------------------------

    def session_cookie_name(self) -> str:
        return self.config.session_cookie_name

    def waiting_page_path(self) -> str:
        return self.config.waiting_page_path

    def admit_callback_path(self) -> str:
        return self.config.admit_callback_path

    def position_stream_path(self) -> str:
        return self.config.position_stream_path

    def status_payload(self, session_id: str) -> Mapping[str, object]:
        snap = self.position(session_id)
        return {
            "session_id": session_id,
            "position": snap.position,
            "queue_size": snap.queue_size,
            "estimated_wait_seconds": snap.estimated_wait_seconds,
            "ready": snap.position == 0,
        }


__all__ = ["WaitingRoom"]
