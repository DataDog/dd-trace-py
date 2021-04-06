import itertools

from ddtrace.vendor import attr

from .. import periodic
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


@attr.s
class RuntimeWorker(periodic.PeriodicService):
    """Worker thread for collecting and writing runtime metrics to a DogStatsd
    client.
    """

    dogstatsd_url = attr.ib(type=str)
    _interval = attr.ib(type=float, factory=lambda: float(get_env("runtime_metrics", "interval", default=10)))
    _dogstatsd_client = attr.ib(init=False, repr=False)
    _runtime_metrics = attr.ib(factory=RuntimeMetrics, repr=False)

    def __attrs_post_init__(self):
        # type: () -> None
        self._dogstatsd_client = get_dogstatsd_client(self.dogstatsd_url)
        # force an immediate update constant tags
        self.update_runtime_tags()
        # Start worker thread
        self.start()

    def flush(self):
        # type: () -> None
        with self._dogstatsd_client:
            for key, value in self._runtime_metrics:
                log.debug("Writing metric %s:%s", key, value)
                self._dogstatsd_client.gauge(key, value)

    def update_runtime_tags(self):
        # type: () -> None
        # DEV: ddstatsd expects tags in the form ['key1:value1', 'key2:value2', ...]
        tags = ["{}:{}".format(k, v) for k, v in RuntimeTags()]
        log.debug("Updating constant tags %s", tags)
        self._dogstatsd_client.constant_tags = tags

    periodic = flush
    on_shutdown = flush
