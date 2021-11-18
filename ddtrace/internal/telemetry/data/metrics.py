import time
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional
from typing import Tuple

from ...hostname import get_hostname


class MetricType:
    """gauge, count, and rate are the 3 metric types accepted by the Telemetry Instrumentation"""

    COUNT = "count"  # type: Literal['count']
    GAUGE = "gauge"  # type: Literal['gauge']
    RATE = "rate"  # type: Literal['rate']


class Series:
    """stores metrics which will be sent to the Telemetry Intake metrics to the Datadog Instrumentation Telemetry Org"""

    def __init__(self, metric, metric_type=MetricType.COUNT, common=False, interval=None):
        # type: (str, Literal["count", "gauge", "rate"], bool, Optional[int]) -> None
        """
        :param str metric: metric name
        :param str metric_type: type of metric (count/gauge/rate)
        :param bool common: set to True if a metric is common to all tracers, false if it is python specific
        :param int interval: field set for gauge and rate metrics, any field set is ignored for count metrics (in secs)

        >>> Series(metric=True, metric_type=MetricType.GAUGE, common=True, interval=10)
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
            "type": self.type,
            "common": self.common,
            "interval": self.interval,
            "host": self.host,
        }
