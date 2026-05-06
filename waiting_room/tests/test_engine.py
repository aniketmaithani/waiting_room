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
