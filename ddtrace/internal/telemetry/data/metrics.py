from enum import Enum
import time
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ...hostname import get_hostname


class MetricType(Enum):
    """gauge, count, and rate are the 3 metric types accepted by the Telemetry Instrumentation"""

    COUNT = "count"  # type: str
    GAUGE = "gauge"  # type: str
    RATE = "rate"  # type: str


class Series:
    """stores metrics which will be sent to the Telemetry Intake metrics to the Datadog Instrumentation Telemetry Org"""

    def __init__(self, metric, metric_type=MetricType.COUNT, common=False, interval=None):
        # type: (str, MetricType, bool, Optional[int]) -> None
        """
        metric: metric name
        metric_type: type of metric (count/gauge/rate)
        common: set to True if a metric is common to all tracers, false if it is python specific
        interval: field set for guage and rate metrics, any field set is ignored for count metrics (in secs)
        """
        self.metric = metric
        self.type = metric_type
        self.interval = interval
        self.common = common
        self.points = []  # type: List[Tuple[int, int]]
        self.tags = {}  # type: Dict[str, str]
        self.host = get_hostname()

    def add_point(self, value):
        # type: (int) -> None
        """adds timestamped data point associated with a metric"""
        timestamp = int(time.time())  # type: int
        self.points.append((timestamp, value))

    def set_tag(self, name, value):
        # type: (str, str) -> None
        """sets a metrics tag"""
        self.tags[name] = value

    def to_dict(self):
        # type: () -> Dict
        """returns a dictionary containing the metrics fields expected by the telemetry intake service"""
        return {
            "metric": self.metric,
            "points": self.points,
            "tags": self.tags,
            "type": self.type.value,
            "common": self.common,
            "interval": self.interval,
            "host": self.host,
        }
