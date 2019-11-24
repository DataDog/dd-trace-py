import collections
import threading

from ..utils.counter import Counter


class Stats(object):
    # Metric types
    METRIC_TYPE_INCREMENT = 'increment'
    METRIC_TYPE_GAUGE = 'gauge'
    METRIC_TYPE_HISTOGRAM = 'histogram'

    # Metric names
    SPANS_OPEN = 'datadog.tracer.spans.open'
    ERROR_LOGS = 'datadog.tracer.log.errors'
    PATCH_ERROR = 'datadog.tracer.patch.error'
    PATCH_SUCCESS = 'datadog.tracer.patch.success'

    def __init__(self):
        self._read_lock = threading.Lock()

        self._last_values = collections.defaultdict(int)
        self._counters = collections.defaultdict(Counter)

    def span_started(self):
        """Increment the number of spans open"""
        self._increment(self.SPANS_OPEN, self.METRIC_TYPE_GAUGE)

    def span_finished(self):
        """Decrement the number of spans open"""
        self._decrement(self.SPANS_OPEN, self.METRIC_TYPE_GAUGE)

    def error_log(self, logger_name):
        """Increment the number of error logs emitted"""
        self._increment(
            self.ERROR_LOGS,
            self.METRIC_TYPE_INCREMENT,
            ('logger:{}'.format(logger_name), ),
        )

    def patch_error(self, module_name):
        """Increment the number of patching errors"""
        self._increment(
            self.PATCH_ERROR,
            self.METRIC_TYPE_GAUGE,
            ('module:{}'.format(module_name), ),
        )

    def patch_success(self, module_name):
        """Increment the number of patching successes"""
        self._increment(
            self.PATCH_SUCCESS,
            self.METRIC_TYPE_GAUGE,
            ('module:{}'.format(module_name), ),
        )

    def _key(self, name, metric_type, tags=None):
        # Tags must be `None` or a `tuple` (`list` is not hashable)
        if tags is not None:
            tags = tuple(tags)
        return (name, metric_type, tags)

    def _increment(self, name, metric_type, tags=None):
        """Internal helper to increment a stats counter"""
        key = self._key(name, metric_type, tags)
        self._counters[key].increment()

    def _decrement(self, name, metric_type, tags=None):
        """Internal helper to decrement a stats counter"""
        key = self._key(name, metric_type, tags)
        self._counters[key].decrement()

    def _get_value(self, name, metric_type, tags=None):
        """Internal helper to get the current value of a counter since last check"""
        key = self._key(name, metric_type, tags)

        # Get the current value and last value we saw
        val = self._counters[key].value(no_lock=True)
        last_value = self._last_values[key]

        # For increments, only grab the difference between
        #   last time we fetched and now
        # For gauges, we always want the current total value
        if metric_type is self.METRIC_TYPE_INCREMENT:
            # Compute the change in value since last check
            val = val - last_value

        # Store the current value for next time we fetch
        self._last_values[key] = val
        return val

    def reset_values(self):
        """
        Return and reset the current value of all counters

        ::

            from ddtrace.internal.stats import get_stats

            # Get global stats instance
            stats = stats.get_stats()

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
                # Convert tags to a list so we can add to default tags set on the dogstatsd client
                if tags is not None:
                    tags = list(tags)

                values.append((name, metric_type, val, tags))

            return values

    def report(self, dogstatsd_client):
        """
        Report all existing metrics to the provided dogstatsd client

        :param dogstatsd_client: A DogStatsd client to send metrics to
        :type dogstatsd_client: :class:`ddtrace.vendor.dogstatsd.DogStatsd`
        """
        for metric, metric_type, value, tags in self.reset_values():
            getattr(dogstatsd_client, metric_type)(metric, value, tags=tags)

            # Manually increment a "sum" counter for histograms
            # DEV: `<metric>.sum` histogram metric is disabled by default in the agent
            if metric_type is self.METRIC_TYPE_HISTOGRAM:
                dogstatsd_client.increment('{}.total'.format(metric), value, tags=tags)


# Default global `Stats` instance
# Get this from `ddtrace.internal.stats.get_stats`
_stats = Stats()


def get_stats():
    """
    Fetch the current global :class:`Stats` instance

    :returns: Global :class:`Stats` instance
    :rtype: :class:`Stats`
    """
    global _stats
    return _stats
