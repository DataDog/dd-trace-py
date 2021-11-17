import time
from typing import Dict
from typing import List
from typing import Optional

from ...hostname import get_hostname


class Series:
    """
    Stores and sends metrics using the Datadog Metrics API: https://docs.datadoghq.com/api/latest/metrics/
    """

    GAUGE = "gauge"
    COUNT = "count"
    RATE = "rate"

    def __init__(self, metric, metric_type=COUNT, common=False, interval=None):
        # type: (str, str, bool, Optional[int]) -> None
        self.metric = metric
        self.type = metric_type
        self.interval = interval
        self.common = common
        self.points = []  # type: List[List[int]]
        self.tags = {}  # type: Dict[str, str]
        self.host = get_hostname()

    def add_point(self, value):
        # type: (int) -> None
        """
        Adds timestamped data point associated with a metric
        """
        timestamp = int(time.time())  # type: int
        self.points.append((timestamp, value))

    def add_tag(self, name, value):
        # type: (str, str) -> None
        """
        Sets a metrics tag
        """
        self.tags[name] = value

    def to_dict(self):
        # type: () -> Dict
        """
        Returns a dictionary containing the metrics fields expected
        by the telemetry intake service
        """
        return {
            "metric": self.metric,
            "points": self.points,
            "tags": self.tags,
            "type": self.type,
            "common": self.common,
            "interval": self.interval,
            "host": self.host,
        }
