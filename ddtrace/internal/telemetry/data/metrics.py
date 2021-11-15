import time
from typing import Dict
from typing import List
from typing import Optional

from ...hostname import get_hostname


class Series:
    GAUGE = "gauge"
    COUNT = "count"
    RATE = "rate"

    TELEMETRY_METRIC_PREFIX = "dd.app_telemetry.tracers.%s"

    def __init__(self, metric, metric_type=COUNT, interval=None):
        # type: (str, str, Optional[int]) -> None
        self.points = []  # type: List[List[int]]
        self.tags = {}  # type: Dict[str, str]
        self.type = metric_type  # type: str
        self.interval = interval  # type: Optional[int]
        self.host = get_hostname()  # type: str
        self.metric = self.TELEMETRY_METRIC_PREFIX % (metric,)  # type: str

    def add_point(self, value):
        # type: (int) -> None
        timestamp = int(time.time())  # type: int
        self.points.append([timestamp, value])

    def add_tag(self, name, value):
        # type: (str, str) -> None
        self.tags[name] = value

    def to_dict(self):
        # type: () -> Dict
        return {
            "metric": self.metric,
            "points": self.points,
            "tags": self.tags,
            "type": self.type,
            "interval": self.interval,
            "host": self.host,
        }
