import itertools
from threading import RLock
from typing import ClassVar
from typing import Optional
from typing import Set
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from ddtrace import Span

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

    _interval = attr.ib(
        type=float, factory=lambda: float(get_env("runtime_metrics", "interval", default=10))  # type: ignore[arg-type]
    )
    tracer = attr.ib(type=ddtrace.Tracer, default=None)
    dogstatsd_url = attr.ib(type=Optional[str], default=None)
    _dogstatsd_client = attr.ib(init=False, repr=False)
    _runtime_metrics = attr.ib(factory=RuntimeMetrics, repr=False)
    _services = attr.ib(type=Set[str], init=False, factory=set)

    _instance = None  # type: ClassVar[Optional[RuntimeWorker]]
    _lock = RLock()

    def __attrs_post_init__(self):
        # type: () -> None
        self._dogstatsd_client = get_dogstatsd_client(self.dogstatsd_url or ddtrace.internal.agent.get_stats_url())
        self.tracer = self.tracer or ddtrace.tracer
        self.tracer.on_start_span(self._set_language_on_span)

    def _set_language_on_span(
        self,
        span,  # type: Span
    ):
        # type: (...) -> None
        # add tags to root span to correlate trace with runtime metrics
        # only applied to spans with types that are internal to applications
        if span.parent_id is None and self.tracer._is_span_internal(span):
            span.meta["language"] = "python"

    @classmethod
    def disable(cls):
        # type: () -> None
        with cls._lock:
            if cls._instance is None:
                return

            forksafe.unregister(cls._restart)

            cls._instance.stop()
            cls._instance.join()
            cls._instance = None

    @classmethod
    def _restart(cls):
        cls.disable()
        cls.enable()

    @classmethod
    def enable(cls, flush_interval=None, tracer=None, dogstatsd_url=None):
        # type: (Optional[float], Optional[ddtrace.Tracer], Optional[str]) -> None
        with cls._lock:
            if cls._instance is not None:
                return
            runtime_worker = cls(flush_interval, tracer, dogstatsd_url)  # type: ignore[arg-type]
            runtime_worker.start()
            # force an immediate update constant tags
            runtime_worker.update_runtime_tags()

            forksafe.register(cls._restart)

            cls._instance = runtime_worker

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
