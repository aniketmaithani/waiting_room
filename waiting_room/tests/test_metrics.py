"""Metrics recorders."""

from __future__ import annotations

import pytest

from waiting_room.core.metrics import NoopMetrics, default_metrics


def test_default_metrics_is_shared() -> None:
    assert default_metrics() is default_metrics()


def test_noop_metrics_accepts_everything() -> None:
    rec = NoopMetrics()
    rec.incr("x", room="a")
    rec.observe("x", 1.0, room="a")
    rec.gauge("x", 1.0, room="a")


def test_prometheus_recorders_share_collectors() -> None:
    pytest.importorskip("prometheus_client")
    from waiting_room.core.metrics import PrometheusMetrics

    first = PrometheusMetrics()
    second = PrometheusMetrics()
    first.incr("test_shared_total", room="a")
    second.incr("test_shared_total", room="b")  # must not raise DuplicatedTimeseries


def test_multiple_rooms_can_coexist(make_room) -> None:
    make_room(name="one").enqueue(ip="1.1.1.1", user_agent="ua")
    make_room(name="two").enqueue(ip="1.1.1.1", user_agent="ua")
