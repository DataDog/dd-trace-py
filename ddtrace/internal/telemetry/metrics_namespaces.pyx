import enum
import time
from typing import Optional
from typing import Tuple

from ddtrace.internal import forksafe
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_DISTRIBUTION
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_GENERATE_METRICS


MetricTagType = Optional[Tuple[Tuple[str, str], ...]]

class MetricType(enum.Enum):
    DISTRIBUTION = "distributions"
    COUNT = "count"
    GAUGE = "gauge"
    RATE = "rate"

cdef class MetricNamespace:
    cdef object _lock
    cdef dict _metrics_data

    def __cinit__(self):
        self._lock = forksafe.Lock()
        self._metrics_data = {}

    cpdef dict flush(self, interval: PyFloat = None):
        with self._lock:
            namespace_metrics, self._metrics_data = self._metrics_data, {}

        now = time.time()
        data = {
            TELEMETRY_TYPE_GENERATE_METRICS: {},
            TELEMETRY_TYPE_DISTRIBUTION: {},
        }
        for metric_id, value in namespace_metrics.items():
            name, namespace, tags, metric_type = metric_id
            tags = ["{}:{}".format(k, v).lower() for k, v in tags] if tags else []
            if metric_type is MetricType.DISTRIBUTION:
                data[TELEMETRY_TYPE_DISTRIBUTION].setdefault(namespace.value, {})[name] = {
                    "metric": name,
                    "type": metric_type.value,
                    "common": True,
                    "points": value,
                    "tags": tags,
                }
            else:
                if metric_type is MetricType.RATE:
                    value = value / interval
                metric = {
                    "metric": name,
                    "type": metric_type.value,
                    "common": True,
                    "points": [[now, value]],
                    "tags": tags,
                }
                if metric_type in (MetricType.RATE, MetricType.GAUGE):
                    metric["interval"] = interval
                data[TELEMETRY_TYPE_GENERATE_METRICS].setdefault(namespace.value, {})[name] = metric

        return data

    def add_metric(
        self,
        metric_type: MetricType,
        namespace: TELEMETRY_NAMESPACE,
        name: str,
        value: PyFloat = 1.0,
        tags: MetricTagType = None,
    ) -> None:
        """
        Telemetry Metrics are stored in DD dashboards, check the metrics in datadoghq.com/metric/explorer.
        The metric will store in dashboard as "dd.instrumentation_telemetry_data." + namespace + "." + name
        """
        cdef tuple metric_id
        metric_id = (name, namespace, tags, metric_type)

        with self._lock:
            if metric_type is MetricType.DISTRIBUTION:
                if metric_id not in self._metrics_data:
                    self._metrics_data[metric_id] = []
                self._metrics_data[metric_id].append((time.time(), value))
            elif metric_type is MetricType.GAUGE:
                self._metrics_data[metric_id] = value
            else:
                self._metrics_data[metric_id] = self._metrics_data.get(metric_id, 0) + value
