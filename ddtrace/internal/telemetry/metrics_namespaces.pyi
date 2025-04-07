import enum
from typing import Optional
from typing import Tuple

from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE

MetricTagType = Optional[Tuple[Tuple[str, str], ...]]

class MetricType(str, enum.Enum):
    DISTRIBUTION = "distributions"
    COUNT = "count"
    GAUGE = "gauge"
    RATE = "rate"

class MetricNamespace:
    def flush(self, interval: float | None = None) -> dict: ...
    def add_metric(
        self,
        metric_type: MetricType,
        namespace: TELEMETRY_NAMESPACE,
        name: str,
        value: float = 1.0,
        tags: MetricTagType = None,
    ) -> None: ...
