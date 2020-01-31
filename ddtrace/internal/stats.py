import collections
import threading

from ..utils.counter import Counter


class Stats(object):
    # Metric types
    METRIC_TYPE_INCREMENT = "increment"
    METRIC_TYPE_GAUGE = "gauge"
    METRIC_TYPE_HISTOGRAM = "histogram"

    # Metric names
    SPANS_OPEN = "datadog.tracer.spans.open"
    ERROR_LOGS = "datadog.tracer.log.errors"
    PATCH_ERROR = "datadog.tracer.patch.error"
    PATCH_SUCCESS = "datadog.tracer.patch.success"

    METRIC_TYPES = {
        SPANS_OPEN: METRIC_TYPE_GAUGE,
        ERROR_LOGS: METRIC_TYPE_INCREMENT,
        PATCH_ERROR: METRIC_TYPE_GAUGE,
        PATCH_SUCCESS: METRIC_TYPE_GAUGE,
    }

    def __init__(self):
        self._read_lock = threading.Lock()

        self._last_values = collections.defaultdict(int)
        self._counters = collections.defaultdict(Counter)
        self._report_once = set()

    def span_started(self):
        """Increment the number of spans open"""
        self._increment(self.SPANS_OPEN)

    def span_finished(self):
        """Decrement the number of spans open"""
        self._decrement(self.SPANS_OPEN)

    def error_log(self, logger_name):
        """Increment the number of error logs emitted"""
        self._increment(
            self.ERROR_LOGS, ("logger:{}".format(logger_name),),
        )

    def patch_error(self, module_name):
        """Increment the number of patching errors"""
        self._increment(
            self.PATCH_ERROR, ("module:{}".format(module_name),), report_once=True,
        )

    def patch_success(self, module_name):
        """Increment the number of patching successes"""
        self._increment(
            self.PATCH_SUCCESS, ("module:{}".format(module_name),), report_once=True,
        )

    def _key(self, name, tags=None):
        # Tags must be `None` or a `tuple` (`list` is not hashable)
        if tags is not None:
            tags = tuple(tags)
        return (name, tags)

    def _increment(self, name, tags=None, report_once=False):
        """Internal helper to increment a stats counter"""
        key = self._key(name, tags)
        self._counters[key].increment()
        if report_once:
            self._report_once.add(key)

    def _decrement(self, name, tags=None, report_once=False):
        """Internal helper to decrement a stats counter"""
        key = self._key(name, tags)
        self._counters[key].decrement()
        if report_once:
            self._report_once.add(key)

    def _get_value(self, name, metric_type, tags=None):
        """Internal helper to get the current value of a counter since last check"""
        key = self._key(name, tags)

        # Get the current value and last value we saw
        val = self._counters[key].value(no_lock=True)
        last_value = self._last_values[key]
        # Store the current value for next time we fetch
        self._last_values[key] = val

        # For increments, only grab the difference between
        #   last time we fetched and now
        # For gauges, we always want the current total value
        if metric_type is self.METRIC_TYPE_INCREMENT:
            # Compute the change in value since last check
            val = val - last_value
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
            for name, tags in self._counters.keys():
                metric_type = self.METRIC_TYPES[name]
                val = self._get_value(name, metric_type, tags)
                # Convert tags to a list so we can add to default tags set on the dogstatsd client
                if tags is not None:
                    tags = list(tags)

                values.append((name, metric_type, val, tags))

            for key in self._report_once:
                if key in self._counters:
                    del self._counters[key]
                if key in self._last_values:
                    del self._last_values[key]
            self._report_once = set()

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
                dogstatsd_client.increment("{}.total".format(metric), value, tags=tags)


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


def span_started():
    """Increment the number of spans open"""
    return get_stats().span_started()


def span_finished():
    """Decrement the number of spans open"""
    return get_stats().span_finished()


def error_log(logger_name):
    """Increment the number of error logs emitted"""
    return get_stats().error_log(logger_name)


def patch_error(module_name):
    """Increment the number of patching errors"""
    return get_stats().patch_error(module_name)


def patch_success(module_name):
    """Increment the number of patching successes"""
    return get_stats().patch_success(module_name)


def report(dogstatsd_client):
    """
    Report all existing metrics to the provided dogstatsd client

    :param dogstatsd_client: A DogStatsd client to send metrics to
    :type dogstatsd_client: :class:`ddtrace.vendor.dogstatsd.DogStatsd`
    """
    return get_stats().report(dogstatsd_client)
