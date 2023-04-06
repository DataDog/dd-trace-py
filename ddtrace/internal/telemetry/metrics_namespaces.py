from typing import Any
from typing import Dict

from ddtrace.internal.telemetry.constants import TELEMETRY_METRIC_TYPE_COUNT
from ddtrace.internal.telemetry.constants import TELEMETRY_METRIC_TYPE_DISTRIBUTIONS
from ddtrace.internal.telemetry.constants import TELEMETRY_METRIC_TYPE_GAUGE
from ddtrace.internal.telemetry.constants import TELEMETRY_METRIC_TYPE_RATE
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_APPSEC
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_TRACER
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_DISTRIBUTION
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_GENERATE_METRICS
from ddtrace.internal.telemetry.metrics import CountMetric
from ddtrace.internal.telemetry.metrics import DistributionMetric
from ddtrace.internal.telemetry.metrics import GaugeMetric
from ddtrace.internal.telemetry.metrics import Metric
from ddtrace.internal.telemetry.metrics import MetricTagType
from ddtrace.internal.telemetry.metrics import MetricType
from ddtrace.internal.telemetry.metrics import RateMetric


NamespaceMetricType = Dict[str, Dict[str, Dict[str, Any]]]


class TelemetryTypeError(Exception):
    pass


class MetricNamespace:
    _metrics_data = {}  # type: NamespaceMetricType
    metric_class = {
        TELEMETRY_METRIC_TYPE_COUNT: CountMetric,
        TELEMETRY_METRIC_TYPE_GAUGE: GaugeMetric,
        TELEMETRY_METRIC_TYPE_RATE: RateMetric,
        TELEMETRY_METRIC_TYPE_DISTRIBUTIONS: DistributionMetric,
    }
    metrics_type_payloads = {
        TELEMETRY_METRIC_TYPE_COUNT: TELEMETRY_TYPE_GENERATE_METRICS,
        TELEMETRY_METRIC_TYPE_GAUGE: TELEMETRY_TYPE_GENERATE_METRICS,
        TELEMETRY_METRIC_TYPE_RATE: TELEMETRY_TYPE_GENERATE_METRICS,
        TELEMETRY_METRIC_TYPE_DISTRIBUTIONS: TELEMETRY_TYPE_DISTRIBUTION,
    }

    def __init__(self):
        self._flush()

    def _flush(self):
        self._metrics_data = {
            TELEMETRY_TYPE_GENERATE_METRICS: {TELEMETRY_NAMESPACE_TAG_TRACER: {}, TELEMETRY_NAMESPACE_TAG_APPSEC: {}},
            TELEMETRY_TYPE_DISTRIBUTION: {TELEMETRY_NAMESPACE_TAG_TRACER: {}, TELEMETRY_NAMESPACE_TAG_APPSEC: {}},
        }

    def get(self):
        # type: () -> Dict[str, Any]
        namespace_metrics = self._metrics_data.copy()
        return namespace_metrics

    @staticmethod
    def validate_type_metric(metric_check, metric_to_validate):
        # type: (Metric, Metric) -> bool
        if metric_check.__class__ == metric_to_validate.__class__:
            return True
        return False

    def _add_metric(self, metric_type, namespace, name, value=1.0, tags={}, interval=10.0):
        # type: (MetricType, str,str, float, MetricTagType, float) -> None
        """
        Telemetry Metrics are stored in DD dashboards, check the metrics in datadoghq.com/metric/explorer.
        The metric will store in dashboard as "dd.instrumentation_telemetry_data." + namespace + "." + name
        """
        metric_check = self.metric_class[metric_type](  # type: ignore[abstract]
            namespace, name, tags=tags, common=True, interval=interval
        )
        metrics_type_payload = self.metrics_type_payloads[metric_type]
        # get a Metric that already exists in _metrics data or create a new one (metric_check)
        metric = self._metrics_data[metrics_type_payload][namespace].get(metric_check.id, metric_check)
        if not self.validate_type_metric(metric_check, metric):
            raise TelemetryTypeError(
                (
                    'Error: metric with name "%s" and type "%s" exists. You can\'t create a new metric '
                    'with this name an type "%s"'
                )
                % (name, metric.metric_type, metric_check.metric_type)
            )
        metric.add_point(value)
        self._metrics_data[metrics_type_payload][namespace][metric.id] = metric
