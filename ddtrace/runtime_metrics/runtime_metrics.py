import threading
import time

from datadog import DogStatsd


from ..internal.logger import get_logger
from ..utils.formats import get_env
from .metric_collectors import (
    GCRuntimeMetricCollector,
    PSUtilRuntimeMetricCollector,
)

DD_METRIC_PREFIX = 'runtime.python'
FLUSH_INTERVAL = 10


log = get_logger(__name__)


# Default metrics to collect
ENABLED_METRICS = set([
    'cpu.time.sys',
    'cpu.time.user',
    'cpu.percent',
    'ctx_switch.voluntary',
    'ctx_switch.involuntary',
    'gc.gen1_count',
    'gc.gen2_count',
    'gc.gen3_count',
    'mem.rss',
    'thread_count',
])

ENABLED_TAGS = set([
    'service',
    'runtime-id',
])


class RuntimeMetricsCollector(object):
    """
    TODO: configuration
    """

    METRIC_COLLECTORS = [
        GCRuntimeMetricCollector,
        PSUtilRuntimeMetricCollector,
    ]

    TAG_COLLECTORS = []

    def __init__(self, hostname, port, runtime_id, services, enabled_metrics=ENABLED_METRICS, enabled_tags=ENABLED_TAGS):
        self._agent_host = hostname
        self._agent_port = port
        self.enabled_metrics = enabled_metrics
        self.enabled_tags = enabled_tags
        self._tracer_tags = [self._metric_tag('runtime-id', runtime_id)]
        self._tracer_tags += [self._metric_tag('service', service) for service in services]

        # Initialize the collectors.
        self._metric_collectors = [collector() for collector in self.METRIC_COLLECTORS]
        self._tag_collectors = [collector() for collector in self.TAG_COLLECTORS]

        self._statsd = DogStatsd(host=self._agent_host, port=self._agent_port,
                                 constant_tags=self._collect_constant_tags())

    def _metric_tag(self, key, value):
        return '{}:{}'.format(key, value)

    def _collect_constant_tags(self):
        """Collects tags to be sent to ddstatsd.

        Note: ddstatsd expects tags in the form ['key1:value1', 'key2:value2', ...]
        """
        tags = list(self._tracer_tags)
        for tag_collector in self._tag_collectors:
            collected_tags = tag_collector.collect(self.enabled_tags)
            tags += [self._metric_tag(k, v) for k, v in collected_tags.items()]
        log.debug('Reporting constant tags {}'.format(tags))
        return tags

    def _collect_metrics(self):
        metrics = {}
        for metric_collector in self._metric_collectors:
            collector_metrics = metric_collector.collect(self.enabled_metrics)
            metrics.update(collector_metrics)
        return metrics

    def flush(self):
        """Collects and flushes enabled metrics to the Datadog Agent."""
        if not self._statsd:
            log.warn('Attempted flush with uninitialized or failed statsd client')
            return

        metrics = self._collect_metrics()

        for metric_key, metric_value in metrics.items():
            metric_key = '{}.{}'.format(DD_METRIC_PREFIX, metric_key)
            log.debug('Flushing metric {}:{}'.format(metric_key, metric_value))
            self._statsd.gauge(metric_key, metric_value)


class RuntimeMetricsCollectorWorker(object):
    def __init__(self, runtime_id, services, flush_interval=FLUSH_INTERVAL):
        self._lock = threading.Lock()
        self._stay_alive = None
        self._thread = None
        self._flush_interval = flush_interval
        self._collector = RuntimeMetricsCollector(runtime_id, services)

    def _target(self):
        import os
        while True:
            log.debug("WORKER {}".format(os.getpid()))
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
            self._collector = RuntimeMetricsCollector(runtime_id, services)

    def stop(self):
        with self._lock:
            self._stay_alive = False
