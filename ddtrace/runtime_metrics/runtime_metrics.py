import threading
import time
import itertools

from ..internal.logger import get_logger
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
    PlatformTagCollector,
)

log = get_logger(__name__)


class RuntimeCollectorsIterable:
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
            self.__class__,
            self._enabled,
        )


class RuntimeTags(RuntimeCollectorsIterable):
    ENABLED = DEFAULT_RUNTIME_TAGS
    COLLECTORS = [
        TracerTagCollector,
        PlatformTagCollector,
    ]


class RuntimeMetrics(RuntimeCollectorsIterable):
    ENABLED = DEFAULT_RUNTIME_METRICS
    COLLECTORS = [
        GCRuntimeMetricCollector,
        PSUtilRuntimeMetricCollector,
    ]


class RuntimeMetricsWorker(object):
    FLUSH_INTERVAL = 10

    def __init__(self, statsd_client, flush_interval=None):
        self._lock = threading.Lock()
        self._stay_alive = None
        self._thread = None
        self._flush_interval = flush_interval or self.FLUSH_INTERVAL
        self._statsd_client = statsd_client
        self._runtime_metrics = RuntimeMetrics()

    def _target(self):
        while True:
            with self._lock:
                if not self._stay_alive:
                    break

                self.flush()
                # try:
                # except Exception as err:
                #     import pdb; pdb.set_trace()
                #     log.error("Failed to flush metrics: {}".format(str(err)))

            time.sleep(self._flush_interval)

    def start(self):
        with self._lock:
            if not self._thread:
                log.debug("Starting {} thread".format(self))
                self._thread = threading.Thread(target=self._target)
                self._thread.setDaemon(True)
                self._thread.start()
                self._stay_alive = True

    def stop(self):
        with self._lock:
            if self._thread and self._stay_alive:
                self._stay_alive = False

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
        with self._lock:
            self._runtime_metrics = RuntimeMetrics()

    def __repr__(self):
        return 'RuntimeMetricsCollectorWorker({})'.format(
            self._runtime_metrics,
        )
