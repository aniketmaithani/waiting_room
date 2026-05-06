"""Redis-backed ``StorageBackend``.

Key layout (room name wrapped in ``{}`` so Redis Cluster co-locates a room's
keys on the same hash slot):

* ``<prefix>:{<room>}:queue``       — sorted set, score=enqueued_at_ms
* ``<prefix>:{<room>}:session:<id>``— hash with metadata, EXPIRE applied
* ``<prefix>:{<room>}:admitted``    — sorted set, score=grace_deadline_ms
* ``<prefix>:{<room>}:killswitch``  — string ``"1"`` when engaged

All multi-step mutations go through Lua scripts loaded from
``waiting_room/core/lua/*.lua`` so concurrent calls cannot interleave.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from importlib import resources
from typing import TYPE_CHECKING, Any

from waiting_room.core._types import (
    Fingerprint,
    QueuePosition,
    Session,
    SessionState,
)
from waiting_room.core.exceptions import BackendUnavailableError
from waiting_room.core.interfaces import StorageBackend

if TYPE_CHECKING:
    import redis as redis_pkg


_LUA_FILES = (
    "enqueue.lua",
    "admit_batch.lua",
    "position.lua",
    "reclaim.lua",
    "release.lua",
)


def _load_lua(name: str) -> str:
    return (resources.files("waiting_room.core.lua") / name).read_text(encoding="utf-8")


def _now_ms() -> int:
    return int(time.time() * 1000)


class RedisStorageBackend(StorageBackend):
    """Production storage backend. Cluster-safe per room via the ``{room}`` hash tag."""

    def __init__(
        self,
        client: redis_pkg.Redis,
        *,
        key_prefix: str = "wr",
    ) -> None:
        self._client = client
        self._prefix = key_prefix.rstrip(":")
        self._scripts: dict[str, Any] = {
            name.removesuffix(".lua"): client.register_script(_load_lua(name))
            for name in _LUA_FILES
        }

    # ---- key helpers ---------------------------------------------------------

    def _room_ns(self, room: str) -> str:
        # `{room}` is a Redis Cluster hash tag — keeps every per-room key on one slot.
        return f"{self._prefix}:{{{room}}}"

    def _queue_key(self, room: str) -> str:
        return f"{self._room_ns(room)}:queue"

    def _session_key(self, room: str, session_id: str) -> str:
        return f"{self._room_ns(room)}:session:{session_id}"

    def _session_prefix(self, room: str) -> str:
        return f"{self._room_ns(room)}:session:"

    def _admitted_key(self, room: str) -> str:
        return f"{self._room_ns(room)}:admitted"

    def _killswitch_key(self, room: str) -> str:
        return f"{self._room_ns(room)}:killswitch"

    # ---- StorageBackend ------------------------------------------------------

    def enqueue(self, room: str, session: Session, score: float) -> int:
        fp = session.fingerprint or Fingerprint("", "")
        hash_args: list[str] = [
            "session_id", session.session_id,
            "state", session.state.value,
            "ip", fp.ip,
            "ua", fp.user_agent_hash,
            "enqueued_at", str(session.enqueued_at),
            "user_id", session.user_id or "",
        ]
        try:
            # Score is a float (seconds since epoch) so sub-ms enqueues preserve order.
            # Redis stores sorted-set scores as doubles natively, so no precision loss.
            position = self._scripts["enqueue"](
                keys=[
                    self._queue_key(room),
                    self._session_key(room, session.session_id),
                    self._killswitch_key(room),
                ],
                args=[
                    session.session_id,
                    repr(float(score)),
                    1800,  # session-hash TTL; engine bumps via update_session
                    *hash_args,
                ],
            )
        except _redis_errors() as exc:
            raise BackendUnavailableError(str(exc)) from exc
        return int(position)

    def position(self, room: str, session_id: str) -> QueuePosition:
        try:
            pos, size = self._scripts["position"](
                keys=[self._queue_key(room)],
                args=[session_id],
            )
        except _redis_errors() as exc:
            raise BackendUnavailableError(str(exc)) from exc
        pos = int(pos)
        size = int(size)
        return QueuePosition(
            position=pos if pos > 0 else (0 if self._is_admitted(room, session_id) else -1),
            queue_size=size,
            estimated_wait_seconds=None,
        )

    def queue_size(self, room: str) -> int:
        try:
            return int(self._client.zcard(self._queue_key(room)))
        except _redis_errors() as exc:
            raise BackendUnavailableError(str(exc)) from exc

    def admit_batch(self, room: str, n: int) -> list[Session]:
        if n <= 0:
            return []
        admitted_at = _now_ms()
        # 60s grace by default; the engine overrides via reclaim_expired with its own value.
        grace_deadline = admitted_at + 60_000
        try:
            sids = self._scripts["admit_batch"](
                keys=[self._queue_key(room), self._admitted_key(room)],
                args=[
                    int(n),
                    admitted_at,
                    grace_deadline,
                    self._session_prefix(room),
                ],
            )
        except _redis_errors() as exc:
            raise BackendUnavailableError(str(exc)) from exc

        out: list[Session] = []
        for raw in sids:
            sid = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
            session = self.get_session(room, sid)
            if session is not None:
                session.state = SessionState.ADMITTED
                out.append(session)
        return out

    def remove(self, room: str, session_id: str) -> bool:
        try:
            removed = self._client.zrem(self._queue_key(room), session_id)
            self._client.delete(self._session_key(room, session_id))
        except _redis_errors() as exc:
            raise BackendUnavailableError(str(exc)) from exc
        return bool(removed)

    def get_session(self, room: str, session_id: str) -> Session | None:
        try:
            raw = self._client.hgetall(self._session_key(room, session_id))
        except _redis_errors() as exc:
            raise BackendUnavailableError(str(exc)) from exc
        if not raw:
            return None
        decoded = _decode_hash(raw)
        try:
            state = SessionState(decoded.get("state", SessionState.QUEUED.value))
        except ValueError:
            state = SessionState.QUEUED
        fp = Fingerprint(
            ip=decoded.get("ip", ""),
            user_agent_hash=decoded.get("ua", ""),
        )
        return Session(
            session_id=decoded.get("session_id", session_id),
            room=room,
            fingerprint=fp,
            enqueued_at=float(decoded.get("enqueued_at") or 0.0),
            state=state,
            user_id=decoded.get("user_id") or None,
        )

    def update_session(
        self,
        room: str,
        session_id: str,
        fields: Mapping[str, str],
    ) -> None:
        if not fields:
            return
        try:
            self._client.hset(
                self._session_key(room, session_id),
                mapping=dict(fields),
            )
        except _redis_errors() as exc:
            raise BackendUnavailableError(str(exc)) from exc

    def admitted_count(self, room: str) -> int:
        try:
            return int(self._client.zcard(self._admitted_key(room)))
        except _redis_errors() as exc:
            raise BackendUnavailableError(str(exc)) from exc

    def release_admission(self, room: str, session_id: str) -> bool:
        try:
            removed = self._scripts["release"](
                keys=[
                    self._admitted_key(room),
                    self._session_key(room, session_id),
                ],
                args=[session_id],
            )
        except _redis_errors() as exc:
            raise BackendUnavailableError(str(exc)) from exc
        return bool(int(removed))

    def reclaim_expired(self, room: str, before_ts: float) -> int:
        try:
            # Queue scores are seconds (set in enqueue); admitted scores are ms
            # (stamped by admit_batch). Pass each side its own cutoff unit.
            count = self._scripts["reclaim"](
                keys=[self._queue_key(room), self._admitted_key(room)],
                args=[repr(float(before_ts)), _now_ms()],
            )
        except _redis_errors() as exc:
            raise BackendUnavailableError(str(exc)) from exc
        return int(count)

    def set_kill_switch(self, room: str, *, engaged: bool) -> None:
        key = self._killswitch_key(room)
        try:
            if engaged:
                self._client.set(key, "1")
            else:
                self._client.delete(key)
        except _redis_errors() as exc:
            raise BackendUnavailableError(str(exc)) from exc

    def is_kill_switch_engaged(self, room: str) -> bool:
        try:
            return self._client.get(self._killswitch_key(room)) in (b"1", "1")
        except _redis_errors() as exc:
            raise BackendUnavailableError(str(exc)) from exc

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except _redis_errors():
            return False

    # ---- helpers -------------------------------------------------------------

    def _is_admitted(self, room: str, session_id: str) -> bool:
        try:
            return self._client.zscore(self._admitted_key(room), session_id) is not None
        except _redis_errors():
            return False


def _decode_hash(raw: Mapping[bytes | str, bytes | str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in raw.items():
        key = k.decode("utf-8") if isinstance(k, (bytes, bytearray)) else str(k)
        val = v.decode("utf-8") if isinstance(v, (bytes, bytearray)) else str(v)
        out[key] = val
    return out


def _redis_errors() -> tuple[type[BaseException], ...]:
    """Lazily resolve redis exception classes — keeps redis a soft dep at import time."""
    try:
        import redis as redis_pkg
    except ImportError:
        return (OSError,)
    return (redis_pkg.RedisError, OSError)


__all__ = ["RedisStorageBackend"]
