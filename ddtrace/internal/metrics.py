from typing import Optional  # noqa:F401
from typing import Protocol  # noqa:F401

from ddtrace.internal.dogstatsd import get_dogstatsd_client
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


class MetricsClient(Protocol):
    def increment(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None: ...
    def gauge(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None: ...
    def histogram(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None: ...
    def distribution(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None: ...


class DogStatsdClient(MetricsClient):
    def __init__(self, namespace: Optional[str] = None) -> None:
        self._client = get_dogstatsd_client(agent_config.dogstatsd_url, namespace=namespace)

    def increment(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        self._client.increment(name, int(value), [":".join(_) for _ in tags.items()] if tags else None)

    def gauge(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        self._client.gauge(name, value, [":".join(_) for _ in tags.items()] if tags else None)

    def histogram(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        self._client.histogram(name, value, [":".join(_) for _ in tags.items()] if tags else None)

    def distribution(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        self._client.distribution(name, value, [":".join(_) for _ in tags.items()] if tags else None)


class InstrumentationTelemetryMetricsClient(MetricsClient):
    def __init__(self, namespace: TELEMETRY_NAMESPACE) -> None:
        self.namespace = namespace

    def increment(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        telemetry_writer.add_count_metric(self.namespace, name, int(value), tuple(tags.items()) if tags else ())

    def gauge(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        telemetry_writer.add_gauge_metric(self.namespace, name, value, tuple(tags.items()) if tags else ())

    def histogram(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        pass

    def distribution(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        telemetry_writer.add_distribution_metric(self.namespace, name, value, tuple(tags.items()) if tags else ())


class Metrics(object):
    """Higher-level DogStatsD interface.

    This class provides automatic handling of namespaces for metrics, with the
    possibility of enabling and disabling them at runtime.

    Example::
        The following example shows how to create the counter metric
        'datadog.tracer.writer.success' and how to increment it. Note that
        metrics are emitted only while the metrics object is enabled.

            >>> tracer_metrics = Metrics(namespace='datadog.tracer')
            >>> tracer_metrics.enable()
            >>> writer_meter = dd_metrics.get_meter('writer')
            >>> writer_meter.increment('success')
            >>> tracer_metrics.disable()
            >>> writer_meter.increment('success')  # won't be emitted
    """

    def __init__(self, client: MetricsClient) -> None:
        self.enabled = False
        self.client = client

    class Meter(object):
        def __init__(self, metrics: "Metrics", name: str) -> None:
            self.metrics = metrics
            self.name = name

        def increment(self, name: str, value: float = 1.0, tags: Optional[dict[str, str]] = None) -> None:
            if not self.metrics.enabled:
                return None

            self.metrics.client.increment(".".join((self.name, name)), value, tags)

        def gauge(self, name: str, value: float = 1.0, tags: Optional[dict[str, str]] = None) -> None:
            if not self.metrics.enabled:
                return None

            self.metrics.client.gauge(".".join((self.name, name)), value, tags)

        def histogram(self, name: str, value: float = 1.0, tags: Optional[dict[str, str]] = None) -> None:
            if not self.metrics.enabled:
                return None

            self.metrics.client.histogram(".".join((self.name, name)), value, tags)

        def distribution(self, name: str, value: float = 1.0, tags: Optional[dict[str, str]] = None) -> None:
            if not self.metrics.enabled:
                return None

            self.metrics.client.distribution(".".join((self.name, name)), value, tags)

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False

    def get_meter(self, name: str) -> "Metrics.Meter":
        return self.Meter(self, name)
