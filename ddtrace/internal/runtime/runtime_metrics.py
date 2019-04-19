import threading
import time
import itertools

from ..logger import get_logger
from .constants import (
    DEFAULT_RUNTIME_METRICS,
    DEFAULT_RUNTIME_TAGS,
)
from .metric_collectors import (
    GCRuntimeMetricCollector,
    PSUtilRuntimeMetricCollector,
)
from .tag_collectors import (
    TracerTagCollector,
)

log = get_logger(__name__)


class RuntimeCollectorsIterable(object):
    def __init__(self, enabled=None):
        self._enabled = enabled or self.ENABLED
        # Initialize the collectors.
        self._collectors = [c() for c in self.COLLECTORS]

    def __iter__(self):
        collected = (
            collector.collect(self._enabled)
            for collector in self._collectors
        )
        return itertools.chain.from_iterable(collected)

    def __repr__(self):
        return '{}(enabled={})'.format(
            self.__class__.__name__,
            self._enabled,
        )


class RuntimeTags(RuntimeCollectorsIterable):
    ENABLED = DEFAULT_RUNTIME_TAGS
    COLLECTORS = [
        TracerTagCollector,
    ]


class RuntimeMetrics(RuntimeCollectorsIterable):
    ENABLED = DEFAULT_RUNTIME_METRICS
    COLLECTORS = [
        GCRuntimeMetricCollector,
        PSUtilRuntimeMetricCollector,
    ]


class RuntimeWorker(object):
    """ Worker thread for collecting and writing runtime metrics to a DogStatsd
        client.
    """

    FLUSH_INTERVAL = 10

    def __init__(self, statsd_client, flush_interval=FLUSH_INTERVAL):
        self._stay_alive = None
        self._thread = None
        self._flush_interval = flush_interval
        self._statsd_client = statsd_client
        self._runtime_metrics = RuntimeMetrics()

    def _target(self):
        while self._stay_alive:
            self.flush()
            time.sleep(self._flush_interval)

    def start(self):
        if not self._thread:
            log.debug('Starting {}'.format(self))
            self._stay_alive = True
            self._thread = threading.Thread(target=self._target)
            self._thread.setDaemon(True)
            self._thread.start()

    def stop(self):
        if self._thread and self._stay_alive:
            log.debug('Stopping {}'.format(self))
            self._stay_alive = False

    def join(self, timeout=None):
        if self._thread:
            return self._thread.join(timeout)

    def _write_metric(self, key, value):
        log.debug('Writing metric {}:{}'.format(key, value))
        self._statsd_client.gauge(key, value)

    def flush(self):
        if not self._statsd_client:
            log.warn('Attempted flush with uninitialized or failed statsd client')
            return

        for key, value in self._runtime_metrics:
            self._write_metric(key, value)

    def reset(self):
        self._runtime_metrics = RuntimeMetrics()

    def __repr__(self):
        return '{}(runtime_metrics={})'.format(
            self.__class__.__name__,
            self._runtime_metrics,
        )
