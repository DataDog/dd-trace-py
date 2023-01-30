from typing import Any
from typing import Dict

from ddtrace.internal.constants import TELEMETRY_APPSEC
from ddtrace.internal.constants import TELEMETRY_TRACER
from ddtrace.internal.telemetry.metrics import CountMetric
from ddtrace.internal.telemetry.metrics import GaugeMetric
from ddtrace.internal.telemetry.metrics import Metric
from ddtrace.internal.telemetry.metrics import MetricTagType
from ddtrace.internal.telemetry.metrics import MetricType
from ddtrace.internal.telemetry.metrics import RateMetric


NamespaceMetricType = Dict[str, Dict[str, Any]]


class TelemetryTypeError(Exception):
    pass


class MetricNamespace:
    _metrics_data = {TELEMETRY_TRACER: {}, TELEMETRY_APPSEC: {}}  # type: NamespaceMetricType
    metric_class = {"count": CountMetric, "gauge": GaugeMetric, "rate": RateMetric}

    def _flush(self):
        self._metrics_data = {TELEMETRY_TRACER: {}, TELEMETRY_APPSEC: {}}

    def get(self):
        # type: () -> Dict[str, Any]
        namespace_metrics = self._metrics_data.copy()
        return namespace_metrics

    def validate_type_metric(self, metric_check, metric_to_validate):
        # type: (Metric, Metric) -> bool
        """ """
        if metric_check.__class__ == metric_to_validate.__class__:
            return True
        return False

    def _add_metric(self, metric_type, namespace, name, value=1.0, tags={}, interval=10.0):
        # type: (MetricType, str,str, float, MetricTagType, float) -> None
        """
        Queues metric
        """
        name = "dd.app_telemetry." + namespace + "." + name
        metric_check = self.metric_class[metric_type](  # type: ignore[abstract]
            namespace, name, metric_type=metric_type, tags=tags, common=True, interval=interval
        )
        metric = self._metrics_data[namespace].get(metric_check.id, metric_check)
        if not self.validate_type_metric(metric_check, metric):
            raise TelemetryTypeError(
                (
                    'Error: metric with name "%s" and type "%s" exists. You can\'t create a new metric '
                    'with this name an type "%s"'
                )
                % (name, metric.type, metric_check.type)
            )
        metric.add_point(value)
        self._metrics_data[namespace][metric.id] = metric
