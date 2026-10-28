from __future__ import annotations

from ddtrace.internal.threads import Lock


class SchemaSampler:
    SAMPLE_INTERVAL_MILLIS: int = 30 * 1000

    def __init__(self) -> None:
        self.weight: int = 0
        self.last_sample_millis: float = 0
        self.lock = Lock()

    def try_sample(self, current_time_millis: float) -> int:
        if current_time_millis >= self.last_sample_millis + self.SAMPLE_INTERVAL_MILLIS:
            with self.lock:
                if current_time_millis >= self.last_sample_millis + self.SAMPLE_INTERVAL_MILLIS:
                    self.last_sample_millis = current_time_millis
                    weight = self.weight
                    self.weight = 0
                    return weight
        return 0

    def can_sample(self, current_time_millis: float) -> bool:
        with self.lock:
            self.weight += 1
        return current_time_millis >= self.last_sample_millis + self.SAMPLE_INTERVAL_MILLIS
