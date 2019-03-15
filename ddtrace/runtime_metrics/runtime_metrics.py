import logging
import threading
import time

from .metric_collectors import (
    GCRuntimeMetricCollector,
    PSUtilRuntimeMetricCollector,
)

AGENT_HOST = '127.0.0.1'
DD_METRIC_PREFIX = 'runtime.python'
FLUSH_INTERVAL = 10
METRIC_AGENT_PORT = 8125


log = logging.getLogger(__name__)


# Default metrics to collect
ENABLED_METRICS = set([
    'ctx_switch.voluntary',
    'ctx_switch.involuntary',
    'cpu.time.sys',
    'cpu.time.user',
    'cpu.percent',
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
        PSUtilRuntimeMetricCollector(),
        GCRuntimeMetricCollector(),
    ]

    TAG_COLLECTORS = [
    ]

    def __init__(self, runtime_id, enabled_metrics=ENABLED_METRICS, enabled_tags=ENABLED_TAGS):
        self._agent_host = AGENT_HOST
        self._agent_metric_port = METRIC_AGENT_PORT
        self.enabled_metrics = enabled_metrics
        self.enabled_tags = enabled_tags
        self._statsd = None
        self._tracer_tags = [
            self._metric_tag('runtime-id', runtime_id),
            # self._metric_tag('service', service),
        ]

        self._init_statsd()

    def _metric_tag(self, key, value):
        return '{}:{}'.format(key, value)

    def _collect_constant_tags(self):
        """Collects tags to be sent to ddstatsd.

        Note: ddstatsd expects tags in the form ['key1:value1', 'key2:value2', ...]
        """
        tags = list(self._tracer_tags)
        for tag_collector in self.TAG_COLLECTORS:
            collected_tags = tag_collector.collect(self.enabled_tags)
            tags += [self._metric_tag(k, v) for k, v in collected_tags.items()]
        log.info('Reporting constant tags {}'.format(tags))
        return tags

    def _collect_metrics(self):
        metrics = {}
        for metric_collector in self.METRIC_COLLECTORS:
            collector_metrics = metric_collector.collect(self.enabled_metrics)
            metrics.update(collector_metrics)
        return metrics

    def _init_statsd(self):
        try:
            from datadog import DogStatsd
            tags = self._collect_constant_tags()
            self._statsd = DogStatsd(host=self._agent_host, port=self._agent_metric_port, constant_tags=tags)
        except ImportError:
            log.info('Install the `datadog` package to enable runtime metrics.')
        except Exception:
            log.warn('Could not initialize ddstatsd.')

    def flush(self):
        """Collects and flushes enabled metrics to the Datadog Agent."""
        if not self._statsd:
            log.warn('Attempted flush with uninitialized or failed statsd client')
            return

        metrics = self._collect_metrics()

        for metric_key, metric_value in metrics.items():
            metric_key = '{}.{}'.format(DD_METRIC_PREFIX, metric_key)
            log.info('Flushing metric {}:{}'.format(metric_key, metric_value))
            self._statsd.gauge(metric_key, metric_value)


class RuntimeMetricsCollectorWorker(object):
    def __init__(self, runtime_id, flush_interval=FLUSH_INTERVAL):
        self._lock = threading.Lock()
        self._stay_alive = None
        self._thread = None
        self._flush_interval = flush_interval
        self.collector = RuntimeMetricsCollector(runtime_id)

    def _target(self):
        while True:
            self.collector.flush()
            with self._lock:
                if self._stay_alive is False:
                    break
            time.sleep(self._flush_interval)

    def start(self):
        if self._thread:
            log.info('Ignoring start as worker already started')
            return
        self._stay_alive = True
        self._thread = threading.Thread(target=self._target)
        self._thread.setDaemon(True)
        self._thread.start()

    def stop(self):
        with self._lock:
            self._stay_alive = False
