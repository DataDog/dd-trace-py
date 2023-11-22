import os
import time
from typing import Dict

from ddtrace.internal.utils.formats import asbool


class deduplication:
    _time_lapse = 3600

    def __init__(self, func):
        self.func = func
        self._last_timestamp: float = time.time()
        self.reported_logs: Dict[int, float] = dict()

    def get_last_time_reported(self, raw_log_hash: int) -> float:
        return self.reported_logs.get(raw_log_hash, 0.0)

    def is_deduplication_enabled(self) -> bool:
        return asbool(os.environ.get("_DD_APPSEC_DEDUPLICATION_ENABLED", "true"))

    def __call__(self, *args, **kwargs):
        result = None
        if self.is_deduplication_enabled() is False:
            result = self.func(*args, **kwargs)
        else:
            raw_log_hash = hash("".join([str(arg) for arg in args]))
            last_reported_timestamp = self.get_last_time_reported(raw_log_hash)
            if time.time() > last_reported_timestamp:
                result = self.func(*args, **kwargs)
                self.reported_logs[raw_log_hash] = time.time() + self._time_lapse
        return result
