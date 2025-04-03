from bm import Scenario

from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.telemetry.metrics import CountMetric
from ddtrace.internal.telemetry.metrics import DistributionMetric
from ddtrace.internal.telemetry.metrics import GaugeMetric
from ddtrace.internal.telemetry.metrics import RateMetric
from ddtrace.internal.telemetry.metrics_namespaces import MetricNamespace


def add_count_metric(namespace: MetricNamespace, name: str):
    namespace.add_metric(
        CountMetric,
        TELEMETRY_NAMESPACE.TRACERS,
        name,
        10,
        tags=(("integration_name", "somevalue"),),
    )


def add_gauge_metric(namespace: MetricNamespace, name: str):
    namespace.add_metric(
        GaugeMetric,
        TELEMETRY_NAMESPACE.TRACERS,
        name,
        10,
        tags=(("integration_name", "somevalue"),),
    )


def add_distribution_metric(namespace: MetricNamespace, name: str):
    namespace.add_metric(
        DistributionMetric,
        TELEMETRY_NAMESPACE.TRACERS,
        name,
        10,
        tags=(("integration_name", "somevalue"),),
    )


def add_rate_metric(namespace: MetricNamespace, name: str):
    namespace.add_metric(
        RateMetric,
        TELEMETRY_NAMESPACE.TRACERS,
        name,
        10,
        tags=(("integration_name", "somevalue"),),
    )


class TelemetryAddMetric(Scenario):
    """
    This scenario checks to see if there's an impact on sending metrics via instrumentation telemetry
    """

    def run(self):
        add_metric = None
        if "count-metric" in self.name:
            add_metric = add_count_metric
        elif "gauge-metric" in self.name:
            add_metric = add_gauge_metric
        elif "distribution-metric" in self.name:
            add_metric = add_distribution_metric
        elif "rate-metric" in self.name:
            add_metric = add_rate_metric
        else:
            raise ValueError(f"Unknown scenario: {self.name}")

        if "create-1-" in self.name:

            def _(loops: int):
                for _ in range(loops):
                    metricnamespace = MetricNamespace()
                    add_metric(metricnamespace, "metric")

        elif "create-100-" in self.name:

            def _(loops: int):
                for _ in range(loops):
                    metricnamespace = MetricNamespace()
                    for i in range(100):
                        add_metric(metricnamespace, str(i))

        elif "increment-1-" in self.name:

            def _(loops: int):
                for _ in range(loops):
                    metricnamespace = MetricNamespace()
                    for _ in range(100):
                        add_metric(metricnamespace, "metric")

        elif "increment-100-" in self.name:

            def _(loops: int):
                for _ in range(loops):
                    metricnamespace = MetricNamespace()
                    for i in range(100):
                        for _ in range(100):
                            add_metric(metricnamespace, str(i))

        else:
            raise ValueError(f"Unknown scenario: {self.name}")

        yield _
