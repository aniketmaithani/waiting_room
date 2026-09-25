"""Metrics adapter.

``prometheus_client`` is a soft dependency: if it isn't installed the
``NoopMetrics`` recorder is used and the host application is unaffected. Hosts
can also pass their own ``MetricsRecorder`` to plug in OpenTelemetry/statsd.
"""

from __future__ import annotations

import functools
import threading
from typing import Any, TypeVar

from waiting_room.core.interfaces import MetricsRecorder

try:  # pragma: no cover - exercised only when prometheus_client is installed
    from prometheus_client import Counter, Gauge, Histogram

    _HAS_PROM = True
except ImportError:  # pragma: no cover
    _HAS_PROM = False

_C = TypeVar("_C")


class NoopMetrics(MetricsRecorder):
    """Drops every metric. Default when prometheus_client is unavailable."""

    def incr(self, name: str, value: float = 1.0, **labels: str) -> None:
        del name, value, labels

    def observe(self, name: str, value: float, **labels: str) -> None:
        del name, value, labels

    def gauge(self, name: str, value: float, **labels: str) -> None:
        del name, value, labels


class PrometheusMetrics(MetricsRecorder):  # pragma: no cover - import-guarded
    """Built-in Prometheus recorder. Lazily declares metrics on first use.

    Collectors live in a process-wide cache keyed by metric name, because
    ``prometheus_client`` rejects registering the same name twice. Every
    recorder (one per room) therefore shares the same collectors and
    distinguishes rooms by label.
    """

    _lock = threading.Lock()
    _collectors: dict[str, Any] = {}

    def __init__(self, namespace: str = "waiting_room") -> None:
        if not _HAS_PROM:
            msg = "prometheus_client is not installed"
            raise ImportError(msg)
        self._ns = namespace

    def _get(self, kind: type[_C], name: str, label_keys: tuple[str, ...]) -> _C:
        full_name = f"{self._ns}_{name}"
        with self._lock:
            collector = self._collectors.get(full_name)
            if collector is None:
                collector = kind(full_name, f"{self._ns} {name}", label_keys)
                self._collectors[full_name] = collector
        if not isinstance(collector, kind):
            msg = f"metric {full_name!r} already registered as {type(collector).__name__}"
            raise TypeError(msg)
        return collector

    def incr(self, name: str, value: float = 1.0, **labels: str) -> None:
        self._get(Counter, name, tuple(sorted(labels))).labels(**labels).inc(value)

    def observe(self, name: str, value: float, **labels: str) -> None:
        self._get(Histogram, name, tuple(sorted(labels))).labels(**labels).observe(value)

    def gauge(self, name: str, value: float, **labels: str) -> None:
        self._get(Gauge, name, tuple(sorted(labels))).labels(**labels).set(value)


@functools.cache
def default_metrics() -> MetricsRecorder:
    """Return the shared Prometheus recorder if available, else a no-op recorder."""
    if _HAS_PROM:
        return PrometheusMetrics()
    return NoopMetrics()


__all__ = ["NoopMetrics", "PrometheusMetrics", "default_metrics"]
