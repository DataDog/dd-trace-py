import logging

from .metric_collectors import (
    GCRuntimeMetricCollector,
    PSUtilRuntimeMetricCollector,
)

DEFAULT_AGENT_HOST = '127.0.0.1'
DEFAULT_METRIC_AGENT_PORT = 8125
DD_METRIC_PREFIX = 'runtime.python'

logging.basicConfig(level=logging.DEBUG)

# TODO: look at gc
# TODO: forking/multi-process app

log = logging.getLogger(__name__)


# Default metrics to collect
DEFAULT_ENABLED_METRICS = set([
    'thread_count',
    'mem.rss',
    'gc.gen1_count',
    'gc.gen2_count',
    'gc.gen3_count',
])

DEFAULT_ENABLED_TAGS = set([
    'service',
    'runtime-id',
])


class RuntimeMetrics(object):
    """
    TODO: configuration
    """

    METRIC_COLLECTORS = [
        PSUtilRuntimeMetricCollector(),
        GCRuntimeMetricCollector(),
    ]

    TAG_COLLECTORS = [
        # RuntimeMetricTagCollector(collect_fn=interpreter_version),
        # RuntimeMetricTagCollector(collect_fn=interpreter_implementation),
        # RuntimeMetricTagCollector(collect_fn=tracer_version),
    ]

    def __init__(self, enabled_metrics=DEFAULT_ENABLED_METRICS, enabled_tags=DEFAULT_ENABLED_TAGS):
        self.agent_host = DEFAULT_AGENT_HOST
        self.agent_metric_port = DEFAULT_METRIC_AGENT_PORT
        self.enabled_metrics = enabled_metrics
        self.enabled_tags = enabled_tags
        self.statsd = None

        self._init_statsd()

    def _collect_constant_tags(self):
        """Collects tags to be sent to ddstatsd.

        Note: ddstatsd expects tags in the form ['key1:value1', 'key2:value2', ...]
        :return:
        """
        tags = []
        for tag_collector in self.TAG_COLLECTORS:
            collected_tags = tag_collector.collect(self.enabled_tags)
            tags += ['{}:{}'.format(k, v) for k, v in collected_tags.items()]
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
            self.statsd = DogStatsd(host=self.agent_host, port=self.agent_metric_port, constant_tags=tags)
        except ImportError:
            log.info('Install the `datadog` package to enable runtime metrics.')
        except Exception:
            log.warn('Could not initialize ddstatsd.')

    def flush(self):
        """Collects and flushes enabled metrics to the Datadog Agent."""
        if not self.statsd:
            log.warn('Attempted flush with uninitialized or failed statsd client')
            return

        metrics = self._collect_metrics()

        for metric_key, metric_value in metrics.items():
            metric_key = '{}.{}'.format(DD_METRIC_PREFIX, metric_key)
            log.info('Flushing metric {}:{}'.format(metric_key, metric_value))
            self.statsd.gauge(metric_key, metric_value)
