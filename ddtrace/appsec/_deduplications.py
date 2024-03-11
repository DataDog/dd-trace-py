from collections import OrderedDict
from time import monotonic

from ddtrace.settings.asm import config as asm_config


M_INF = float("-inf")


class deduplication:
    _time_lapse = 3600  # 1 hour
    _max_cache_size = 256

    def __init__(self, func):
        self.func = func
        self.reported_logs: OrderedDict[int, float] = OrderedDict()

    def get_last_time_reported(self, raw_log_hash: int) -> float:
        return self.reported_logs.get(raw_log_hash, 0.0)

    def __call__(self, *args, **kwargs):
        result = None
        if asm_config._deduplication_enabled:
            raw_log_hash = hash("".join([str(arg) for arg in args]))
            last_reported_timestamp = self.reported_logs.get(raw_log_hash, M_INF)
            current = monotonic()
            if current > last_reported_timestamp:
                result = self.func(*args, **kwargs)
                self.reported_logs[raw_log_hash] = current + self._time_lapse
                self.reported_logs.move_to_end(raw_log_hash)
                if len(self.reported_logs) >= self._max_cache_size:
                    self.reported_logs.popitem(last=False)
        else:
            result = self.func(*args, **kwargs)
        return result
