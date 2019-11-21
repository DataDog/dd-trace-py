import collections
import threading

from ..utils.counter import Counter


class Stats(object):
    SPANS_STARTED = 'datadog.tracer.spans.started'
    SPANS_STARTED_TYPE = 'increment'

    SPANS_FINISHED = 'datadog.tracer.spans.finished'
    SPANS_FINISHED_TYPE = 'increment'

    ERROR_LOGS = 'datadog.tracer.log.errors'
    ERROR_LOGS_TYPE = 'increment'

    PATCH_ERROR = 'datadog.tracer.patch.error'
    PATCH_ERROR_TYPE = 'increment'

    PATCH_SUCCESS = 'datadog.tracer.patch.success'
    PATCH_SUCCESS_TYPE = 'increment'

    def __init__(self):
        self._read_lock = threading.Lock()

        self._last_values = collections.defaultdict(int)
        self._counters = collections.defaultdict(Counter)
        self._one_time_stats = set()

    def span_started(self):
        """Increment the number of spans started"""
        self._increment(self.SPANS_STARTED, self.SPANS_STARTED_TYPE)

    def span_finished(self):
        """Increment the number of spans finished"""
        self._increment(self.SPANS_FINISHED, self.SPANS_FINISHED_TYPE)

    def error_log(self, logger_name):
        """Increment the number of error logs emitted"""
        self._increment(
            self.ERROR_LOGS,
            self.ERROR_LOGS_TYPE,
            ('logger:{}'.format(logger_name), ),
        )

    def patch_error(self, module_name):
        """Increment the number of patching errors"""
        self._increment(
            self.PATCH_ERROR,
            self.PATCH_ERROR_TYPE,
            ('module:{}'.format(module_name), ),
            one_time=True,
        )

    def patch_success(self, module_name):
        """Increment the number of patching successes"""
        self._increment(
            self.PATCH_SUCCESS,
            self.PATCH_SUCCESS_TYPE,
            ('module:{}'.format(module_name), ),
            one_time=True,
        )

    def _key(self, name, metric_type, tags=None):
        if tags is not None:
            tags = tuple(tags)
        return (name, metric_type, tags)

    def _increment(self, name, metric_type, tags=None, one_time=False):
        """Internal helper to increment a stats counter"""
        key = self._key(name, metric_type, tags)
        self._counters[key].increment()
        if one_time:
            self._one_time_stats.add(key)

    def _get_value(self, name, metric_type, tags=None):
        """Internal helper to get the current value of a counter since last check"""
        key = self._key(name, metric_type, tags)

        # Get the current value and last value we saw
        current_value = self._counters[key].value(no_lock=True)
        last_value = self._last_values[key]

        # Compute the change in value since last check
        val = current_value - last_value

        # Store the current value for next time we fetch
        self._last_values[key] = current_value
        return val

    def reset_values(self):
        """
        Return and reset the current value of all counters

        ::

            from ddtrace.internal.stats import stats

            # Increment counters
            stats.span_started()
            stats.span_finished()

            # Fetch all current metrics, resetting their internal values back to 0
            for metric_name, metric_type, value, tags in stats.reset_values():
                pass

        :returns: List of ``(metric_name, metric_type, value, tags)`` for each stat monitored
        :rtype: :obj:`list`
        """
        with self._read_lock:
            # Collect and reset all current counters
            values = []
            for name, metric_type, tags in self._counters.keys():
                val = self._get_value(name, metric_type, tags)
                if tags is not None:
                    tags = list(tags)

                values.append((name, metric_type, val, tags))

            # Remove any one time keys
            for key in self._one_time_stats:
                del self._counters[key]
                del self._last_values[key]
            self._one_time_stats = set()

            return values

    def report(self, dogstatsd_client):
        """
        Report all existing metrics to the provided dogstatsd client

        :param dogstatsd_client: A DogStatsd client to send metrics to
        :type dogstatsd_client: :class:`ddtrace.vendor.dogstatsd.DogStatsd`
        """
        for metric, metric_type, value, tags in self.reset_values():
            if metric_type == 'increment':
                dogstatsd_client.increment(metric, value, tags=tags)
            elif metric_type == 'gauge':
                dogstatsd_client.gauge(metric, value, tags=tags)
            elif metric_type == 'histogram':
                dogstatsd_client.histogram(metric, value, tags=tags)


stats = Stats()
