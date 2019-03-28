import threading
import time
import itertools

from datadog import DogStatsd

from ..internal.logger import get_logger
from ..utils.formats import get_env
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

class RuntimeCollectorsIterable(object):
    def __init__(self, enabled=None):
        self._enabled = enabled or self.ENABLED
        # Initialize the collectors.
        self._collectors = [c() for c in self.COLLECTORS]

    def __iter__(self):
        collected = [
            collector.collect(self._enabled)
            for collector in self._collectors
        ]
        return itertools.chain.from_iterable(collected)

    def __repr__(self):
        return '{}(enabled={})'.format(
            self.__name__,
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

    def __init__(self, hostname, port, runtime_id, services, flush_interval=FLUSH_INTERVAL):
        self._lock = threading.Lock()
        self._stay_alive = None
        self._thread = None
        self._flush_interval = flush_interval
        self._hostname = hostname
        self._port = port
        self._collector = RuntimeMetrics()

    def _target(self):
        while True:
            with self._lock:
                if not self._stay_alive:
                    break
                self._collector.flush()
            time.sleep(self._flush_interval)

    def start(self):
        if self._thread:
            log.debug('Ignoring start as worker already started')
            return

        self._stay_alive = True
        self._thread = threading.Thread(target=self._target)
        self._thread.setDaemon(True)
        self._thread.start()

    def reset(self, runtime_id, services):
        with self._lock:
            self._collector = RuntimeMetricsCollector(self._hostname, self._port, runtime_id, services)

    def stop(self):
        with self._lock:
            self._stay_alive = False

    def __repr__(self):
        return 'RuntimeMetricsCollectorWorker({})'.format(
            self._collector,
        )
