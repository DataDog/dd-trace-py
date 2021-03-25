import itertools

from ... import _worker
from ...utils.formats import get_env
from ..dogstatsd import get_dogstatsd_client
from ..logger import get_logger
from .constants import DEFAULT_RUNTIME_METRICS
from .constants import DEFAULT_RUNTIME_TAGS
from .metric_collectors import GCRuntimeMetricCollector
from .metric_collectors import PSUtilRuntimeMetricCollector
from .tag_collectors import PlatformTagCollector
from .tag_collectors import TracerTagCollector


log = get_logger(__name__)


class RuntimeCollectorsIterable(object):
    def __init__(self, enabled=None):
        self._enabled = enabled or self.ENABLED
        # Initialize the collectors.
        self._collectors = [c() for c in self.COLLECTORS]

    def __iter__(self):
        collected = (collector.collect(self._enabled) for collector in self._collectors)
        return itertools.chain.from_iterable(collected)

    def __repr__(self):
        return "{}(enabled={})".format(
            self.__class__.__name__,
            self._enabled,
        )


class RuntimeTags(RuntimeCollectorsIterable):
    ENABLED = DEFAULT_RUNTIME_TAGS
    COLLECTORS = [
        PlatformTagCollector,
        TracerTagCollector,
    ]


class RuntimeMetrics(RuntimeCollectorsIterable):
    ENABLED = DEFAULT_RUNTIME_METRICS
    COLLECTORS = [
        GCRuntimeMetricCollector,
        PSUtilRuntimeMetricCollector,
    ]


class RuntimeWorker(_worker.PeriodicWorkerThread):
    """Worker thread for collecting and writing runtime metrics to a DogStatsd
    client.
    """

    FLUSH_INTERVAL = 10

    def __init__(self, dogstatsd_url, flush_interval=None):
        super(RuntimeWorker, self).__init__(
            interval=flush_interval or float(get_env("runtime_metrics", "interval", default=self.FLUSH_INTERVAL)),
            name=self.__class__.__name__,
        )
        self._dogstatsd_client = get_dogstatsd_client(dogstatsd_url)
        # Initialize collector
        self._runtime_metrics = RuntimeMetrics()
        # force an immediate update constant tags
        self.update_runtime_tags()
        # Start worker thread
        self.start()

    def flush(self):
        with self._dogstatsd_client:
            for key, value in self._runtime_metrics:
                log.debug("Writing metric %s:%s", key, value)
                self._dogstatsd_client.gauge(key, value)

    def update_runtime_tags(self):
        # DEV: ddstatsd expects tags in the form ['key1:value1', 'key2:value2', ...]
        tags = ["{}:{}".format(k, v) for k, v in RuntimeTags()]
        log.debug("Updating constant tags %s", tags)
        self._dogstatsd_client.constant_tags = tags

    run_periodic = flush
    on_shutdown = flush

    def __repr__(self):
        return "{}(runtime_metrics={})".format(
            self.__class__.__name__,
            self._runtime_metrics,
        )
