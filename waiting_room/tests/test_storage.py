"""Redis storage backend (Lua atomicity)."""

from __future__ import annotations

import time

import fakeredis
import pytest

from waiting_room.core._types import AdmissionLimits, Fingerprint, Session, SessionState
from waiting_room.core.storage import RedisStorageBackend

UNLIMITED = AdmissionLimits()


@pytest.fixture
def backend(redis_client: fakeredis.FakeRedis) -> RedisStorageBackend:
    return RedisStorageBackend(redis_client, key_prefix="wr")


def _make_session(sid: str = "s1") -> Session:
    return Session(
        session_id=sid,
        room="test",
        fingerprint=Fingerprint("1.2.3.4", "abc"),
    )


def test_enqueue_returns_position(backend: RedisStorageBackend) -> None:
    p1 = backend.enqueue("test", _make_session("s1"), score=time.time())
    p2 = backend.enqueue("test", _make_session("s2"), score=time.time() + 0.1)
    assert (p1, p2) == (1, 2)
    assert backend.queue_size("test") == 2


def test_position_returns_zero_when_admitted(backend: RedisStorageBackend) -> None:
    backend.enqueue("test", _make_session("s1"), score=time.time())
    admitted = backend.admit_batch("test", n=1, limits=UNLIMITED)
    assert admitted == ["s1"]
    session = backend.get_session("test", "s1")
    assert session is not None
    assert session.state == SessionState.ADMITTED
    pos = backend.position("test", "s1")
    assert pos.position == 0  # admitted, not in queue


def test_position_minus_one_for_unknown(backend: RedisStorageBackend) -> None:
    pos = backend.position("test", "ghost")
    assert pos.position == -1


def test_admit_batch_is_atomic_under_concurrency(
    backend: RedisStorageBackend,
    redis_client: fakeredis.FakeRedis,
) -> None:
    # Enqueue 100 sessions, then try to admit "more than exists" twice in succession.
    # Each call must only return what's actually there — never duplicate sessions.
    for i in range(100):
        backend.enqueue("test", _make_session(f"s{i}"), score=time.time() + i * 0.0001)

    first = backend.admit_batch("test", n=60, limits=UNLIMITED)
    second = backend.admit_batch("test", n=60, limits=UNLIMITED)
    assert len(first) == 60
    assert len(second) == 40
    seen = set(first) | set(second)
    assert len(seen) == 100  # no duplicates
    assert backend.queue_size("test") == 0
    assert backend.admitted_count("test") == 100


def test_admit_batch_empty_queue(backend: RedisStorageBackend) -> None:
    assert backend.admit_batch("test", n=10, limits=UNLIMITED) == []


def test_kill_switch_blocks_enqueue(backend: RedisStorageBackend) -> None:
    backend.set_kill_switch("test", engaged=True)
    assert backend.is_kill_switch_engaged("test") is True
    pos = backend.enqueue("test", _make_session("s1"), score=time.time())
    assert pos == -1
    assert backend.queue_size("test") == 0


def test_release_admission_frees_slot(backend: RedisStorageBackend) -> None:
    backend.enqueue("test", _make_session("s1"), score=time.time())
    backend.admit_batch("test", n=1, limits=UNLIMITED)
    assert backend.admitted_count("test") == 1
    assert backend.release_admission("test", "s1") is True
    assert backend.admitted_count("test") == 0


def test_reclaim_drops_old_sessions(backend: RedisStorageBackend) -> None:
    old_ts = time.time() - 1_000
    fresh_ts = time.time()
    backend.enqueue("test", _make_session("old"), score=old_ts)
    backend.enqueue("test", _make_session("fresh"), score=fresh_ts)
    reclaimed = backend.reclaim_expired("test", before_ts=time.time() - 100)
    assert reclaimed >= 1
    assert backend.queue_size("test") == 1
    assert backend.position("test", "fresh").position == 1


def test_get_session_round_trip(backend: RedisStorageBackend) -> None:
    backend.enqueue("test", _make_session("s1"), score=time.time())
    s = backend.get_session("test", "s1")
    assert s is not None
    assert s.session_id == "s1"
    assert s.fingerprint is not None
    assert s.fingerprint.ip == "1.2.3.4"


def test_remove_drops_session(backend: RedisStorageBackend) -> None:
    backend.enqueue("test", _make_session("s1"), score=time.time())
    assert backend.remove("test", "s1") is True
    assert backend.queue_size("test") == 0
    assert backend.get_session("test", "s1") is None


def test_ping(backend: RedisStorageBackend) -> None:
    assert backend.ping() is True


def _fill(backend: RedisStorageBackend, count: int) -> None:
    for i in range(count):
        backend.enqueue("test", _make_session(f"s{i}"), score=time.time() + i * 0.0001)


def test_admit_batch_respects_capacity(backend: RedisStorageBackend) -> None:
    _fill(backend, 10)
    limits = AdmissionLimits(capacity=3)
    assert len(backend.admit_batch("test", n=10, limits=limits)) == 3
    assert backend.admit_batch("test", n=10, limits=limits) == []
    assert backend.admitted_count("test") == 3


def test_admit_batch_rate_is_shared_across_callers(
    redis_client: fakeredis.FakeRedis,
) -> None:
    # Two backends model two worker processes pointing at the same Redis.
    a = RedisStorageBackend(redis_client, key_prefix="wr")
    b = RedisStorageBackend(redis_client, key_prefix="wr")
    _fill(a, 20)
    limits = AdmissionLimits(rate_per_second=2, burst=2)
    admitted = a.admit_batch("test", n=20, limits=limits) + b.admit_batch(
        "test", n=20, limits=limits
    )
    assert len(admitted) == 2


def test_admit_batch_rate_below_one_per_second_still_admits(
    backend: RedisStorageBackend,
) -> None:
    _fill(backend, 3)
    limits = AdmissionLimits(rate_per_second=0.5)
    assert len(backend.admit_batch("test", n=3, limits=limits)) == 1


def test_admit_batch_frees_expired_slots(
    backend: RedisStorageBackend,
    redis_client: fakeredis.FakeRedis,
) -> None:
    _fill(backend, 2)
    limits = AdmissionLimits(capacity=1)
    assert backend.admit_batch("test", n=1, limits=limits) == ["s0"]
    # Grace deadline passes and nobody runs the reclaim command.
    redis_client.zadd("wr:{test}:admitted", {"s0": 1})
    assert backend.admit_batch("test", n=1, limits=limits) == ["s1"]
    expired = backend.get_session("test", "s0")
    assert expired is not None
    assert expired.state == SessionState.EXPIRED


def test_admit_batch_halts_while_kill_switch_engaged(backend: RedisStorageBackend) -> None:
    _fill(backend, 2)
    backend.set_kill_switch("test", engaged=True)
    assert backend.admit_batch("test", n=2, limits=UNLIMITED) == []
    assert backend.queue_size("test") == 2


def test_admit_batch_skips_sessions_without_metadata(
    backend: RedisStorageBackend,
    redis_client: fakeredis.FakeRedis,
) -> None:
    _fill(backend, 2)
    redis_client.delete("wr:{test}:session:s0")
    assert backend.admit_batch("test", n=2, limits=UNLIMITED) == ["s1"]
    assert backend.admitted_count("test") == 1
