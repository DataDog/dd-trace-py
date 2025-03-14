from bm import Scenario

from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.telemetry.metrics_namespaces import MetricNamespace

try:
    from ddtrace.internal.telemetry.metrics_namespaces import MetricType

    CountMetric = MetricType.COUNT
    DistributionMetric = MetricType.DISTRIBUTION
    GaugeMetric = MetricType.GAUGE
    RateMetric = MetricType.RATE
except ImportError:
    from ddtrace.internal.telemetry.metrics import CountMetric
    from ddtrace.internal.telemetry.metrics import DistributionMetric
    from ddtrace.internal.telemetry.metrics import GaugeMetric
    from ddtrace.internal.telemetry.metrics import RateMetric


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
        elif self.name.startswith("flush-"):
            pass
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

        elif self.name.startswith("flush-"):
            should_flush = not self.name.endswith("-baseline")

            metrics = (
                [(f"count-{i}", add_count_metric) for i in range(250)]
                + [(f"gauge-{i}", add_gauge_metric) for i in range(250)]
                + [(f"distribution-{i}", add_distribution_metric) for i in range(250)]
                + [(f"rate-{i}", add_rate_metric) for i in range(250)]
            )

            if self.name.startswith("flush-1-"):

                def _(loops: int):
                    for _ in range(loops):
                        metricnamespace = MetricNamespace()
                        metrics[0][1](metricnamespace, metrics[0][0])
                        if should_flush:
                            metricnamespace.flush()

            elif self.name.startswith("flush-100-"):

                def _(loops: int):
                    for _ in range(loops):
                        metricnamespace = MetricNamespace()
                        for i in range(0, 1000, 100):
                            metrics[i][1](metricnamespace, metrics[i][0])
                        if should_flush:
                            metricnamespace.flush()
            elif self.name.startswith("flush-1000-"):

                def _(loops: int):
                    for _ in range(loops):
                        metricnamespace = MetricNamespace()
                        for metric_name, add_metric in metrics:
                            add_metric(metricnamespace, metric_name)
                        if should_flush:
                            metricnamespace.flush()
            else:
                raise ValueError(f"Unknown scenario: {self.name}")

        else:
            raise ValueError(f"Unknown scenario: {self.name}")

        yield _
