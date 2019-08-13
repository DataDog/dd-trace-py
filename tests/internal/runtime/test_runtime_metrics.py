import time

from ddtrace.internal.runtime.runtime_metrics import (
    RuntimeTags,
    RuntimeMetrics,
    RuntimeWorker,
)
from ddtrace.internal.runtime.constants import (
    DEFAULT_RUNTIME_METRICS,
    DEFAULT_RUNTIME_TAGS,
    GC_COUNT_GEN0,
    SERVICE
)
from ddtrace.vendor.dogstatsd import DogStatsd

from ...base import (
    BaseTestCase,
    BaseTracerTestCase,
)
from ...utils.tracer import FakeSocket


class TestRuntimeTags(BaseTracerTestCase):
    def test_all_tags(self):
        with self.override_global_tracer():
            with self.trace('test', service='test'):
                tags = set([k for (k, v) in RuntimeTags()])
                self.assertSetEqual(tags, DEFAULT_RUNTIME_TAGS)

    def test_one_tag(self):
        with self.override_global_tracer():
            with self.trace('test', service='test'):
                tags = [k for (k, v) in RuntimeTags(enabled=[SERVICE])]
                self.assertEqual(tags, [SERVICE])


class TestRuntimeMetrics(BaseTestCase):
    def test_all_metrics(self):
        metrics = set([k for (k, v) in RuntimeMetrics()])
        self.assertSetEqual(metrics, DEFAULT_RUNTIME_METRICS)

    def test_one_metric(self):
        metrics = [k for (k, v) in RuntimeMetrics(enabled=[GC_COUNT_GEN0])]
        self.assertEqual(metrics, [GC_COUNT_GEN0])


class TestRuntimeWorker(BaseTracerTestCase):
    def test_tracer_metrics(self):
        # mock dogstatsd client before configuring tracer for runtime metrics
        self.tracer._dogstatsd_client = DogStatsd()
        self.tracer._dogstatsd_client.socket = FakeSocket()

        default_flush_interval = RuntimeWorker.FLUSH_INTERVAL
        try:
            # lower flush interval
            RuntimeWorker.FLUSH_INTERVAL = 1./4

            # configure tracer for runtime metrics
            self.tracer.configure(collect_metrics=True)
        finally:
            # reset flush interval
            RuntimeWorker.FLUSH_INTERVAL = default_flush_interval

        with self.override_global_tracer(self.tracer):
            root = self.start_span('parent', service='parent')
            context = root.context
            self.start_span('child', service='child', child_of=context)

            time.sleep(self.tracer._runtime_worker.interval * 2)
            self.tracer._runtime_worker.stop()
            self.tracer._runtime_worker.join()

            # get all received metrics
            received = []
            while True:
                new = self.tracer._dogstatsd_client.socket.recv()
                if not new:
                    break

                received.append(new)

            # expect received all default metrics
            # we expect more than one flush since it is also called on shutdown
            assert len(received) / len(DEFAULT_RUNTIME_METRICS) > 1

            # expect all metrics in default set are received
            # DEV: dogstatsd gauges in form "{metric_name}:{metric_value}|g#t{tag_name}:{tag_value},..."
            self.assertSetEqual(
                set([gauge.split(':')[0] for gauge in received]),
                DEFAULT_RUNTIME_METRICS
            )

            # check to last set of metrics returned to confirm tags were set
            for gauge in received[-len(DEFAULT_RUNTIME_METRICS):]:
                self.assertRegexpMatches(gauge, 'service:parent')
                self.assertRegexpMatches(gauge, 'service:child')
