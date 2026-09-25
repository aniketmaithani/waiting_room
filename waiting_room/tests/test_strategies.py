"""Admission strategies."""

from __future__ import annotations

import time

import pytest

from waiting_room.core.strategies import (
    CapacityAwareAdmission,
    CompositeAdmission,
    TimeBucketAdmission,
)


def test_time_bucket_initial_burst() -> None:
    s = TimeBucketAdmission(admit_per_second=10, burst=10)
    n = s.slots_available(queue_size=100, admitted=0, capacity=100)
    assert n == 10  # full bucket on first call


def test_time_bucket_replenishes_over_time() -> None:
    s = TimeBucketAdmission(admit_per_second=20, burst=2)
    s.slots_available(queue_size=100, admitted=0, capacity=100)  # drain
    time.sleep(0.2)
    n = s.slots_available(queue_size=100, admitted=0, capacity=100)
    assert n >= 2  # ~4 tokens regenerated, capped by burst


def test_time_bucket_zero_when_queue_empty() -> None:
    s = TimeBucketAdmission(admit_per_second=10, burst=10)
    assert s.slots_available(queue_size=0, admitted=0, capacity=100) == 0


def test_capacity_aware_respects_admitted() -> None:
    s = CapacityAwareAdmission()
    assert s.slots_available(queue_size=100, admitted=80, capacity=100) == 20
    assert s.slots_available(queue_size=100, admitted=100, capacity=100) == 0


def test_composite_takes_minimum() -> None:
    s = CompositeAdmission(
        TimeBucketAdmission(admit_per_second=100, burst=100),
        CapacityAwareAdmission(),
    )
    n = s.slots_available(queue_size=200, admitted=95, capacity=100)
    assert n == 5  # capacity-limited despite 100 tokens available


def test_invalid_rate_raises() -> None:
    with pytest.raises(ValueError, match="admit_per_second"):
        TimeBucketAdmission(admit_per_second=0)


def test_composite_requires_at_least_one() -> None:
    with pytest.raises(ValueError, match="at least one"):
        CompositeAdmission()


def test_time_bucket_below_one_per_second_admits() -> None:
    s = TimeBucketAdmission(admit_per_second=0.5)
    assert s.slots_available(queue_size=10, admitted=0, capacity=0) == 1
