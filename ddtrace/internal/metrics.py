from typing import Dict  # noqa:F401
from typing import Optional  # noqa:F401

from ddtrace.internal.dogstatsd import get_dogstatsd_client
from ddtrace.settings._agent import config as agent_config


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

    def __init__(self, dogstats_url=None, namespace=None):
        # type: (Optional[str], Optional[str]) -> None
        self.dogstats_url = dogstats_url
        self.namespace = namespace
        self.enabled = False

        self._client = get_dogstatsd_client(dogstats_url or agent_config.dogstatsd_url, namespace=namespace)

    class Meter(object):
        def __init__(self, metrics, name):
            # type: (Metrics, str) -> None
            self.metrics = metrics
            self.name = name

        def increment(self, name, value=1.0, tags=None):
            # type: (str, float, Optional[Dict[str, str]]) -> None
            if not self.metrics.enabled:
                return None

            self.metrics._client.increment(
                ".".join((self.name, name)), value, [":".join(_) for _ in tags.items()] if tags else None
            )

        def gauge(self, name, value=1.0, tags=None):
            # type: (str, float, Optional[Dict[str, str]]) -> None
            if not self.metrics.enabled:
                return None

            self.metrics._client.gauge(
                ".".join((self.name, name)), value, [":".join(_) for _ in tags.items()] if tags else None
            )

        def histogram(self, name, value=1.0, tags=None):
            # type: (str, float, Optional[Dict[str, str]]) -> None
            if not self.metrics.enabled:
                return None

            self.metrics._client.histogram(
                ".".join((self.name, name)), value, [":".join(_) for _ in tags.items()] if tags else None
            )

        def distribution(self, name, value=1.0, tags=None):
            # type: (str, float, Optional[Dict[str, str]]) -> None
            if not self.metrics.enabled:
                return None

            self.metrics._client.distribution(
                ".".join((self.name, name)), value, [":".join(_) for _ in tags.items()] if tags else None
            )

    def enable(self):
        # type: () -> None
        self.enabled = True

    def disable(self):
        # type: () -> None
        self.enabled = False

    def get_meter(self, name):
        # type: (str) -> Metrics.Meter
        return self.Meter(self, name)
