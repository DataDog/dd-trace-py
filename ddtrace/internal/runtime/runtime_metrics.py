import itertools
from threading import RLock
from typing import Optional
from typing import Set

import attr

import ddtrace
from ddtrace.internal import forksafe

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


@attr.s(eq=False)
class RuntimeWorker(periodic.PeriodicService):
    """Worker thread for collecting and writing runtime metrics to a DogStatsd
    client.
    """

    _interval = attr.ib(type=float, factory=lambda: float(get_env("runtime_metrics", "interval", default=10)))
    tracer = attr.ib(type=Optional[ddtrace.Tracer], default=None)
    dogstatsd_url = attr.ib(type=Optional[str], default=None)
    _dogstatsd_client = attr.ib(init=False, repr=False)
    _runtime_metrics = attr.ib(factory=RuntimeMetrics, repr=False)
    _services = attr.ib(type=Set[str], init=False)

    _instance = None  # type: Optional[RuntimeWorker]
    _lock = RLock()

    def __attrs_post_init__(self):
        # type: () -> None
        self._dogstatsd_client = get_dogstatsd_client(self.dogstatsd_url or ddtrace.internal.agent.get_stats_url())
        self.tracer = self.tracer or ddtrace.tracer
        self.tracer.on_start_span(self._set_language_on_span)
        self._services = set()

    def _set_language_on_span(self, span):
        # add tags to root span to correlate trace with runtime metrics
        # only applied to spans with types that are internal to applications
        if span.parent_id is None and self.tracer._is_span_internal(span):
            span.meta["language"] = "python"

    @staticmethod
    def disable():
        # type: () -> None
        with RuntimeWorker._lock:
            if RuntimeWorker._instance is None:
                return

            forksafe.unregister(RuntimeWorker._restart)

            RuntimeWorker._instance.stop()
            RuntimeWorker._instance.join()
            RuntimeWorker._instance = None

    @staticmethod
    def _restart():
        RuntimeWorker.disable()
        RuntimeWorker.enable()

    @staticmethod
    def enable(flush_interval=None, tracer=None, dogstatsd_url=None):
        # type: (Optional[float], Optional[ddtrace.Tracer], Optional[str]) -> None
        with RuntimeWorker._lock:
            if RuntimeWorker._instance is not None:
                return

            runtime_worker = RuntimeWorker(flush_interval, tracer, dogstatsd_url)
            runtime_worker.start()
            # force an immediate update constant tags
            runtime_worker.update_runtime_tags()

            forksafe.register(RuntimeWorker._restart)

            RuntimeWorker._instance = runtime_worker

    def flush(self):
        # type: () -> None
        # The constant tags for the dogstatsd client needs to updated with any new
        # service(s) that may have been added.
        if self._services != self.tracer._services:
            self._services = self.tracer._services
            self.update_runtime_tags()

        with self._dogstatsd_client:
            for key, value in self._runtime_metrics:
                log.debug("Writing metric %s:%s", key, value)
                self._dogstatsd_client.gauge(key, value)

    def stop(self):
        # De-register span hook
        self.tracer.deregister_on_start_span(self._set_language_on_span)
        super(RuntimeWorker, self).stop()

    def update_runtime_tags(self):
        # type: () -> None
        # DEV: ddstatsd expects tags in the form ['key1:value1', 'key2:value2', ...]
        tags = ["{}:{}".format(k, v) for k, v in RuntimeTags()]
        log.debug("Updating constant tags %s", tags)
        self._dogstatsd_client.constant_tags = tags

    periodic = flush
    on_shutdown = flush
