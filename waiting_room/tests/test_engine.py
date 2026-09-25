"""End-to-end engine flow."""

from __future__ import annotations

import pytest

from waiting_room.core._types import EventType, SessionState
from waiting_room.core.events import InProcessEventEmitter
from waiting_room.core.exceptions import (
    InvalidTokenError,
    KillSwitchEngagedError,
    TokenAlreadyUsedError,
)


def test_enqueue_assigns_position(make_room) -> None:
    room = make_room()
    s, snap = room.enqueue(ip="1.2.3.4", user_agent="ua")
    assert s.session_id
    assert s.state == SessionState.QUEUED
    assert snap.position == 1
    assert snap.queue_size == 1


def test_admission_round_trip(make_room) -> None:
    room = make_room(admit_per_second=100, capacity=10)
    s, _ = room.enqueue(ip="1.2.3.4", user_agent="ua")
    ticket = room.try_admit(s.session_id)
    assert ticket is not None
    redeemed = room.redeem(ticket.token, ip="1.2.3.4", user_agent="ua")
    assert redeemed.session_id == s.session_id


def test_redeem_rejects_token_for_different_fingerprint(make_room) -> None:
    room = make_room(admit_per_second=100, capacity=10)
    s, _ = room.enqueue(ip="1.2.3.4", user_agent="ua")
    ticket = room.try_admit(s.session_id)
    assert ticket is not None
    with pytest.raises(InvalidTokenError):
        room.redeem(ticket.token, ip="9.9.9.9", user_agent="ua")


def test_redeem_is_single_use(make_room) -> None:
    room = make_room(admit_per_second=100, capacity=10)
    s, _ = room.enqueue(ip="1.2.3.4", user_agent="ua")
    ticket = room.try_admit(s.session_id)
    assert ticket is not None
    room.redeem(ticket.token, ip="1.2.3.4", user_agent="ua")
    with pytest.raises(TokenAlreadyUsedError):
        room.redeem(ticket.token, ip="1.2.3.4", user_agent="ua")


def test_kill_switch_blocks_new_entries(make_room) -> None:
    room = make_room()
    room.set_kill_switch(engaged=True)
    assert room.is_kill_switch_engaged() is True
    with pytest.raises(KillSwitchEngagedError):
        room.enqueue(ip="1.2.3.4", user_agent="ua")


def test_capacity_caps_admissions(make_room) -> None:
    room = make_room(admit_per_second=1000, capacity=3)
    sids = [room.enqueue(ip="1.2.3.4", user_agent="ua")[0].session_id for _ in range(5)]
    admitted = []
    for sid in sids:
        t = room.try_admit(sid)
        if t is not None:
            admitted.append(sid)
    assert len(admitted) == 3


def test_release_frees_a_slot(make_room) -> None:
    room = make_room(admit_per_second=1000, capacity=2)
    s1, _ = room.enqueue(ip="1", user_agent="ua")
    s2, _ = room.enqueue(ip="2", user_agent="ua")
    s3, _ = room.enqueue(ip="3", user_agent="ua")
    assert room.try_admit(s1.session_id) is not None
    assert room.try_admit(s2.session_id) is not None
    assert room.try_admit(s3.session_id) is None  # capacity full
    assert room.release(s1.session_id) is True
    assert room.try_admit(s3.session_id) is not None


def test_emitter_fires_lifecycle_events(make_room) -> None:
    room = make_room(admit_per_second=100, capacity=10)
    received: list[tuple] = []
    emitter = room.emitter
    assert isinstance(emitter, InProcessEventEmitter)
    emitter.subscribe(lambda et, payload: received.append((et, dict(payload))))

    s, _ = room.enqueue(ip="1.2.3.4", user_agent="ua")
    room.try_admit(s.session_id)
    types = {e[0] for e in received}
    assert EventType.ENQUEUED in types
    assert EventType.ADMITTED in types


def test_allowlist_check(make_room) -> None:
    room = make_room()
    room.config.allowlist_ips = ("10.0.0.1",)  # type: ignore[misc]
    assert room.is_allowlisted(ip="10.0.0.1", user_id=None) is True
    assert room.is_allowlisted(ip="9.9.9.9", user_id=None) is False


def test_capacity_aware_policy_uses_its_own_capacity(redis_client, secret) -> None:
    from waiting_room.core.engine import WaitingRoom
    from waiting_room.core.settings import AdmissionPolicy, WaitingRoomConfig

    cfg = WaitingRoomConfig(
        name="cap",
        secret_key=secret,
        target_url="/x",
        capacity=100,
        policy=AdmissionPolicy.capacity_aware(capacity=2),
        rate_limit_per_ip_per_minute=1_000_000,
    )
    room = WaitingRoom(cfg, redis_client=redis_client)
    assert room.effective_capacity == 2
    sids = [room.enqueue(ip=f"10.0.0.{i}", user_agent="ua")[0].session_id for i in range(4)]
    admitted = [sid for sid in sids if room.try_admit(sid) is not None]
    assert len(admitted) == 2


def test_rate_is_shared_between_engine_instances(make_room) -> None:
    first = make_room(admit_per_second=1, capacity=0)
    second = make_room(admit_per_second=1, capacity=0)
    s1, _ = first.enqueue(ip="1.1.1.1", user_agent="ua")
    s2, _ = second.enqueue(ip="2.2.2.2", user_agent="ua")
    results = [first.try_admit(s1.session_id), second.try_admit(s2.session_id)]
    assert sum(r is not None for r in results) == 1


def test_custom_strategy_narrows_admissions(make_room) -> None:
    from waiting_room.core.interfaces import AdmissionStrategy

    class _Nobody(AdmissionStrategy):
        def slots_available(self, *, queue_size: int, admitted: int, capacity: int) -> int:
            return 0

    room = make_room()
    room._strategy = _Nobody()
    s, _ = room.enqueue(ip="1.1.1.1", user_agent="ua")
    assert room.try_admit(s.session_id) is None


def test_reclaim_uses_idle_time_not_join_time(make_room, redis_client) -> None:
    room = make_room(admit_per_second=0.001, capacity=0)
    room.config.queued_session_ttl_seconds = 60
    s, _ = room.enqueue(ip="1.1.1.1", user_agent="ua")
    redis_client.zadd("wr:{test}:queue", {s.session_id: 1.0})  # joined long ago
    room.position(s.session_id)  # but still polling
    assert room.reclaim() == 0
    assert room.position(s.session_id).position == 1


def test_reenqueue_keeps_place_for_same_client(make_room) -> None:
    room = make_room(admit_per_second=0.001, capacity=0)
    first, _ = room.enqueue(ip="1.1.1.1", user_agent="ua")
    room.enqueue(ip="2.2.2.2", user_agent="ua")
    again, snap = room.enqueue(ip="1.1.1.1", user_agent="ua", existing_session_id=first.session_id)
    assert again.session_id == first.session_id
    assert snap.position == 1


def test_reenqueue_with_foreign_session_id_gets_new_session(make_room) -> None:
    room = make_room(admit_per_second=0.001, capacity=0)
    victim, _ = room.enqueue(ip="1.1.1.1", user_agent="ua")
    thief, snap = room.enqueue(
        ip="6.6.6.6",
        user_agent="evil",
        existing_session_id=victim.session_id,
    )
    assert thief.session_id != victim.session_id
    assert snap.position == 2
    assert room.position(victim.session_id).position == 1


@pytest.mark.parametrize("bogus", ["", "not-a-sid", "A" * 32, "x" * 500, "{other}:queue"])
def test_malformed_session_ids_are_ignored(make_room, bogus: str) -> None:
    room = make_room()
    session, _ = room.enqueue(ip="1.1.1.1", user_agent="ua", existing_session_id=bogus)
    assert session.session_id != bogus
    assert len(session.session_id) == 32


def test_reenqueue_of_admitted_session_does_not_requeue(make_room) -> None:
    room = make_room(admit_per_second=100, capacity=10)
    s, _ = room.enqueue(ip="1.1.1.1", user_agent="ua")
    assert room.try_admit(s.session_id) is not None
    again, snap = room.enqueue(ip="1.1.1.1", user_agent="ua", existing_session_id=s.session_id)
    assert again.state == SessionState.ADMITTED
    assert snap.position == 0
    assert room.position(s.session_id).position == 0


def _admit(room, ip: str = "1.2.3.4", ua: str = "ua"):
    s, _ = room.enqueue(ip=ip, user_agent=ua)
    ticket = room.try_admit(s.session_id)
    assert ticket is not None
    return s, ticket


def test_redeem_returns_reusable_pass(make_room) -> None:
    room = make_room(admit_per_second=100, capacity=10)
    s, ticket = _admit(room)
    admission_pass = room.redeem(ticket.token, ip="1.2.3.4", user_agent="ua")
    for _ in range(3):
        verified = room.verify_pass(admission_pass.token, ip="1.2.3.4", user_agent="ua")
        assert verified.session_id == s.session_id


def test_pass_is_bound_to_fingerprint(make_room) -> None:
    room = make_room(admit_per_second=100, capacity=10)
    _, ticket = _admit(room)
    admission_pass = room.redeem(ticket.token, ip="1.2.3.4", user_agent="ua")
    with pytest.raises(InvalidTokenError):
        room.verify_pass(admission_pass.token, ip="9.9.9.9", user_agent="ua")


def test_ticket_and_pass_are_not_interchangeable(make_room) -> None:
    room = make_room(admit_per_second=100, capacity=10)
    _, ticket = _admit(room)
    with pytest.raises(InvalidTokenError):
        room.verify_pass(ticket.token, ip="1.2.3.4", user_agent="ua")
    admission_pass = room.redeem(ticket.token, ip="1.2.3.4", user_agent="ua")
    with pytest.raises(InvalidTokenError):
        room.redeem(admission_pass.token, ip="1.2.3.4", user_agent="ua")


def test_ticket_from_another_room_is_rejected(make_room) -> None:
    room_a = make_room(name="a", admit_per_second=100, capacity=10)
    room_b = make_room(name="b", admit_per_second=100, capacity=10)
    _, ticket = _admit(room_a)
    with pytest.raises(InvalidTokenError, match="different room"):
        room_b.redeem(ticket.token, ip="1.2.3.4", user_agent="ua")


def test_redeem_holds_slot_for_admitted_session_ttl(make_room, redis_client) -> None:
    import time

    room = make_room(admit_per_second=100, capacity=10)
    s, ticket = _admit(room)
    room.redeem(ticket.token, ip="1.2.3.4", user_agent="ua")
    deadline_ms = redis_client.zscore("wr:{test}:admitted", s.session_id)
    expected = (time.time() + room.config.admitted_session_ttl_seconds) * 1000
    assert abs(deadline_ms - expected) < 5_000


def test_redeem_after_admission_lapsed_is_rejected(make_room, redis_client) -> None:
    room = make_room(admit_per_second=100, capacity=10)
    s, ticket = _admit(room)
    redis_client.zrem("wr:{test}:admitted", s.session_id)  # grace window passed
    with pytest.raises(InvalidTokenError, match="lapsed"):
        room.redeem(ticket.token, ip="1.2.3.4", user_agent="ua")


def test_fail_open_ticket_is_bound_to_caller(make_room) -> None:
    from waiting_room.core.exceptions import BackendUnavailableError
    from waiting_room.core.settings import FailureMode

    room = make_room(failure_mode=FailureMode.FAIL_OPEN)

    def _down(*_args: object, **_kwargs: object) -> None:
        raise BackendUnavailableError

    room._storage.get_session = _down
    ticket = room.try_admit("a" * 32, ip="1.2.3.4", user_agent="ua")
    assert ticket is not None
    verified = room._signer.verify(ticket.token, fingerprint=room._fingerprint("1.2.3.4", "ua"))
    assert verified.session_id == "a" * 32


def test_status_polling_admits_the_front_of_the_queue(make_room) -> None:
    room = make_room(admit_per_second=100, capacity=10)
    s, _ = room.enqueue(ip="1.2.3.4", user_agent="ua")
    payload = room.status_payload(s.session_id)
    assert payload["ready"] is True
    assert room.try_admit(s.session_id) is not None


def test_status_polling_ticks_are_throttled(make_room) -> None:
    room = make_room()
    calls: list[int] = []
    original = room._storage.admit_batch

    def _counting(*args: object, **kwargs: object) -> list[str]:
        calls.append(1)
        return original(*args, **kwargs)

    room._storage.admit_batch = _counting
    s, _ = room.enqueue(ip="1.2.3.4", user_agent="ua")
    room.status_payload(s.session_id)
    room.status_payload(s.session_id)
    assert len(calls) == 1


def test_status_reports_closed_room(make_room) -> None:
    room = make_room(admit_per_second=0.001, capacity=0)
    s, _ = room.enqueue(ip="1.2.3.4", user_agent="ua")
    room.set_kill_switch(engaged=True)
    payload = room.status_payload(s.session_id)
    assert payload["closed"] is True
    assert payload["ready"] is False


def test_stats_snapshot(make_room) -> None:
    room = make_room(capacity=7)
    room.enqueue(ip="1.2.3.4", user_agent="ua")
    stats = room.stats()
    assert stats.queue_size == 1
    assert stats.capacity == 7
    assert stats.kill_switch_engaged is False
    assert stats.healthy is True


def test_flush_clears_room(make_room) -> None:
    room = make_room()
    room.enqueue(ip="1.2.3.4", user_agent="ua")
    assert room.flush() > 0
    assert room.stats().queue_size == 0


def test_allowlist_accepts_cidr_networks(make_room) -> None:
    room = make_room()
    room.config.allowlist_ips = ("10.0.0.0/8", "2001:db8::/32")
    assert room.is_allowlisted(ip="10.1.2.3", user_id=None) is True
    assert room.is_allowlisted(ip="2001:db8::1", user_id=None) is True
    assert room.is_allowlisted(ip="11.0.0.1", user_id=None) is False
    assert room.is_allowlisted(ip="not-an-ip", user_id=None) is False


def test_invalid_allowlist_entry_is_rejected(secret) -> None:
    from waiting_room.core.settings import WaitingRoomConfig

    with pytest.raises(ValueError, match="allowlist_ips"):
        WaitingRoomConfig(name="x", secret_key=secret, target_url="/", allowlist_ips=("nope",))
