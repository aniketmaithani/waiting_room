"""Audit log: the engine emits events → AdmissionEvent rows are persisted."""

from __future__ import annotations

import fakeredis
import pytest

from waiting_room.adapters.django import registry
from waiting_room.adapters.django.models import AdmissionEvent
from waiting_room.adapters.django.signals import make_handler, waiting_room_event
from waiting_room.core.engine import WaitingRoom
from waiting_room.core.events import InProcessEventEmitter
from waiting_room.core.settings import (
    AdmissionPolicy,
    RedisConfig,
    WaitingRoomConfig,
)


@pytest.fixture
def audit_room() -> WaitingRoom:
    cfg = WaitingRoomConfig(
        name="audit",
        secret_key="x" * 64,
        target_url="/checkout/",
        capacity=5,
        policy=AdmissionPolicy.time_bucket(admit_per_second=100, burst=100),
        storage=RedisConfig(url="redis://fake/0"),
        rate_limit_per_ip_per_minute=1_000_000,
    )
    room = WaitingRoom(cfg, redis_client=fakeredis.FakeRedis())
    assert isinstance(room.emitter, InProcessEventEmitter)
    room.emitter.subscribe(make_handler("audit"))
    registry.register("audit", room)
    yield room
    registry.reset()


@pytest.mark.django_db
def test_enqueue_writes_audit_row(audit_room: WaitingRoom) -> None:
    audit_room.enqueue(ip="1.2.3.4", user_agent="ua")
    rows = list(AdmissionEvent.objects.filter(room="audit", event_type="enqueued"))
    assert len(rows) == 1
    assert rows[0].position == 1


@pytest.mark.django_db
def test_admit_writes_audit_row(audit_room: WaitingRoom) -> None:
    s, _ = audit_room.enqueue(ip="1.2.3.4", user_agent="ua")
    audit_room.try_admit(s.session_id)
    types = {r.event_type for r in AdmissionEvent.objects.filter(room="audit")}
    assert {"enqueued", "admitted"}.issubset(types)


@pytest.mark.django_db
def test_django_signal_fires(audit_room: WaitingRoom) -> None:
    received: list[tuple[str, str]] = []

    def handler(sender: str, event_type: str, payload, **kwargs) -> None:
        received.append((sender, event_type))

    waiting_room_event.connect(handler)
    try:
        audit_room.enqueue(ip="1.2.3.4", user_agent="ua")
    finally:
        waiting_room_event.disconnect(handler)
    assert ("audit", "enqueued") in received
