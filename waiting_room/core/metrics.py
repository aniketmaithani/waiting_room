"""Metrics adapter.

``prometheus_client`` is a soft dependency: if it isn't installed the
``NoopMetrics`` recorder is used and the host application is unaffected. Hosts
can also pass their own ``MetricsRecorder`` to plug in OpenTelemetry/statsd.
"""

from __future__ import annotations

from waiting_room.core.interfaces import MetricsRecorder

try:  # pragma: no cover - exercised only when prometheus_client is installed
    from prometheus_client import Counter, Gauge, Histogram

    _HAS_PROM = True
except ImportError:  # pragma: no cover
    _HAS_PROM = False


class NoopMetrics(MetricsRecorder):
    """Drops every metric. Default when prometheus_client is unavailable."""

    def incr(self, name: str, value: float = 1.0, **labels: str) -> None:
        del name, value, labels

    def observe(self, name: str, value: float, **labels: str) -> None:
        del name, value, labels

    def gauge(self, name: str, value: float, **labels: str) -> None:
        del name, value, labels


class PrometheusMetrics(MetricsRecorder):  # pragma: no cover - import-guarded
    """Built-in Prometheus recorder. Lazily declares metrics on first use."""

    def __init__(self, namespace: str = "waiting_room") -> None:
        if not _HAS_PROM:
            msg = "prometheus_client is not installed"
            raise ImportError(msg)
        self._ns = namespace
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._hists: dict[str, Histogram] = {}

    def _counter(self, name: str, label_keys: tuple[str, ...]) -> Counter:
        if name not in self._counters:
            self._counters[name] = Counter(
                f"{self._ns}_{name}",
                f"{self._ns} counter {name}",
                label_keys,
            )
        return self._counters[name]

    def _gauge(self, name: str, label_keys: tuple[str, ...]) -> Gauge:
        if name not in self._gauges:
            self._gauges[name] = Gauge(
                f"{self._ns}_{name}",
                f"{self._ns} gauge {name}",
                label_keys,
            )
        return self._gauges[name]

    def _hist(self, name: str, label_keys: tuple[str, ...]) -> Histogram:
        if name not in self._hists:
            self._hists[name] = Histogram(
                f"{self._ns}_{name}",
                f"{self._ns} histogram {name}",
                label_keys,
            )
        return self._hists[name]

    def incr(self, name: str, value: float = 1.0, **labels: str) -> None:
        self._counter(name, tuple(sorted(labels))).labels(**labels).inc(value)

    def observe(self, name: str, value: float, **labels: str) -> None:
        self._hist(name, tuple(sorted(labels))).labels(**labels).observe(value)

    def gauge(self, name: str, value: float, **labels: str) -> None:
        self._gauge(name, tuple(sorted(labels))).labels(**labels).set(value)


def default_metrics() -> MetricsRecorder:
    """Pick the Prometheus recorder if it's available, else the no-op."""
    if _HAS_PROM:
        return PrometheusMetrics()
    return NoopMetrics()


__all__ = ["NoopMetrics", "PrometheusMetrics", "default_metrics"]
