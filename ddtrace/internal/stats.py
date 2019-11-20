import collections
import itertools
import threading


class Stats(object):
    SPANS_STARTED = 'datadog.tracer.spans.started'
    SPANS_FINISHED = 'datadog.tracer.spans.finished'
    ERROR_LOGS = 'datadog.tracer.log.errors'
    PATCH_ERROR = 'datadog.tracer.patch.error'
    PATCH_SUCCESS = 'datadog.tracer.patch.success'

    def __init__(self):
        self._read_lock = threading.Lock()

        self._last_values = collections.defaultdict(int)
        self._values = collections.defaultdict(itertools.count)
        self._one_time_stats = set()

    def span_started(self):
        self._increment(self.SPANS_STARTED)

    def span_finished(self):
        self._increment(self.SPANS_FINISHED)

    def error_log(self, logger_name):
        self._increment(
            self.ERROR_LOGS,
            ('logger:{}'.format(logger_name), ),
        )

    def patch_error(self, module_name):
        self._increment(
            self.PATCH_ERROR,
            ('module:{}'.format(module_name), ),
            one_time=True,
        )

    def patch_success(self, module_name):
        self._increment(
            self.PATCH_SUCCESS,
            ('module:{}'.format(module_name), ),
            one_time=True,
        )

    def _increment(self, name, tags=None, one_time=False):
        key = (name, tags)
        next(self._values[key])
        if one_time:
            self._one_time_stats.add(key)

    def _get_value(self, name, tags=None):
        key = (name, tags)

        current_value = next(self._values[key])
        last_value = self._last_values[key]

        val = current_value - last_value
        self._last_values[key] = current_value + 1
        return val

    def reset_values(self):
        with self._read_lock:
            values = [
                (name, self._get_value(name, tags), tags)
                for (name, tags) in self._values.keys()
            ]

            # Remove any one time keys
            for key in self._one_time_stats:
                del self._values[key]
                del self._last_values[key]
            self._one_time_stats = set()

            return values


stats = Stats()
