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
    RUNTIME_ID,
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
                tags = [k for (k, v) in RuntimeTags(enabled=[RUNTIME_ID])]
                self.assertEqual(tags, [RUNTIME_ID])


class TestRuntimeMetrics(BaseTestCase):
    def test_all_metrics(self):
        metrics = set([k for (k, v) in RuntimeMetrics()])
        self.assertSetEqual(metrics, DEFAULT_RUNTIME_METRICS)

    def test_one_metric(self):
        metrics = [k for (k, v) in RuntimeMetrics(enabled=[GC_COUNT_GEN0])]
        self.assertEqual(metrics, [GC_COUNT_GEN0])


class TestRuntimeWorker(BaseTracerTestCase):
    def test_worker_metrics(self):
        self.tracer.configure(collect_metrics=True)

        with self.override_global_tracer(self.tracer):
            self.tracer._dogstatsd_client = DogStatsd()
            self.tracer._dogstatsd_client.socket = FakeSocket()

            root = self.start_span('parent', service='parent')
            context = root.context
            self.start_span('child', service='child', child_of=context)

            self.worker = RuntimeWorker(self.tracer._dogstatsd_client)
            self.worker.start()
            self.worker.stop()

            # get all received metrics
            received = []
            while True:
                new = self.tracer._dogstatsd_client.socket.recv()
                if not new:
                    break

                received.append(new)
                # DEV: sleep since metrics will still be getting collected and written
                time.sleep(.5)

            # expect received all default metrics
            self.assertEqual(len(received), len(DEFAULT_RUNTIME_METRICS))

            # expect all metrics in default set are received
            # DEV: dogstatsd gauges in form "{metric_name}:{metric_value}|g#t{tag_name}:{tag_value},..."
            self.assertSetEqual(
                set([gauge.split(':')[0] for gauge in received]),
                DEFAULT_RUNTIME_METRICS
            )

            for gauge in received:
                self.assertRegexpMatches(gauge, 'runtime-id:')
                self.assertRegexpMatches(gauge, 'service:parent')
                self.assertRegexpMatches(gauge, 'service:child')
