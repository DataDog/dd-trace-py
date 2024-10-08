import threading


class SchemaSampler:
    SAMPLE_INTERVAL_MILLIS = 30 * 1000

    def __init__(self):
        self.weight = 0
        self.last_sample_millis = 0
        self.lock = threading.Lock()

    def try_sample(self, current_time_millis):
        if current_time_millis >= self.last_sample_millis + self.SAMPLE_INTERVAL_MILLIS:
            with self.lock:
                if current_time_millis >= self.last_sample_millis + self.SAMPLE_INTERVAL_MILLIS:
                    self.last_sample_millis = current_time_millis
                    weight = self.weight
                    self.weight = 0
                    return weight
        return 0

    def can_sample(self, current_time_millis):
        with self.lock:
            self.weight += 1
        return current_time_millis >= self.last_sample_millis + self.SAMPLE_INTERVAL_MILLIS
