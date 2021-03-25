import time

import mock

from ddtrace.ext import SpanTypes
from ddtrace.internal.runtime.constants import DEFAULT_RUNTIME_METRICS
from ddtrace.internal.runtime.constants import ENV
from ddtrace.internal.runtime.constants import GC_COUNT_GEN0
from ddtrace.internal.runtime.constants import SERVICE
from ddtrace.internal.runtime.runtime_metrics import RuntimeMetrics
from ddtrace.internal.runtime.runtime_metrics import RuntimeTags
from tests.utils import BaseTestCase
from tests.utils import TracerTestCase
from tests.utils import override_env


class TestRuntimeTags(TracerTestCase):
    def test_all_tags(self):
        with self.override_global_tracer():
            with self.trace("test", service="test"):
                tags = set([k for (k, v) in RuntimeTags()])
                assert SERVICE in tags
                # no env set by default
                assert ENV not in tags

    def test_one_tag(self):
        with self.override_global_tracer():
            with self.trace("test", service="test"):
                tags = [k for (k, v) in RuntimeTags(enabled=[SERVICE])]
                self.assertEqual(tags, [SERVICE])

    def test_env_tag(self):
        def filter_only_env_tags(tags):
            return [(k, v) for (k, v) in RuntimeTags() if k == "env"]

        with self.override_global_tracer():
            # first without env tag set in tracer
            with self.trace("first-test", service="test"):
                tags = filter_only_env_tags(RuntimeTags())
                assert tags == []

            # then with an env tag set
            self.tracer.set_tags({"env": "tests.dog"})
            with self.trace("second-test", service="test"):
                tags = filter_only_env_tags(RuntimeTags())
                assert tags == [("env", "tests.dog")]

            # check whether updating env works
            self.tracer.set_tags({"env": "staging.dog"})
            with self.trace("third-test", service="test"):
                tags = filter_only_env_tags(RuntimeTags())
                assert tags == [("env", "staging.dog")]


class TestRuntimeMetrics(BaseTestCase):
    def test_all_metrics(self):
        metrics = set([k for (k, v) in RuntimeMetrics()])
        self.assertSetEqual(metrics, DEFAULT_RUNTIME_METRICS)

    def test_one_metric(self):
        metrics = [k for (k, v) in RuntimeMetrics(enabled=[GC_COUNT_GEN0])]
        self.assertEqual(metrics, [GC_COUNT_GEN0])


class TestRuntimeWorker(TracerTestCase):
    def test_tracer_metrics(self):
        # Mock socket.socket to hijack the dogstatsd socket
        with mock.patch("socket.socket"):
            # configure tracer for runtime metrics
            interval = 1.0 / 4
            with override_env(dict(DD_RUNTIME_METRICS_INTERVAL=str(interval))):
                self.tracer.configure(collect_metrics=True)
                self.tracer.set_tags({"env": "tests.dog"})

                with self.override_global_tracer(self.tracer):
                    # spans are started for three services but only web and worker
                    # span types should be included in tags for runtime metrics
                    root = self.start_span("parent", service="parent", span_type=SpanTypes.WEB)
                    context = root.context
                    child = self.start_span("child", service="child", span_type=SpanTypes.WORKER, child_of=context)
                    self.start_span("query", service="db", span_type=SpanTypes.SQL, child_of=child.context)
                    time.sleep(interval * 4)
                    # Get the mocked socket for inspection later
                    statsd_socket = self.tracer._runtime_worker._dogstatsd_client.socket
                    # now stop collection
                    self.tracer.configure(collect_metrics=False)

                received = [s.args[0].decode("utf-8") for s in statsd_socket.send.mock_calls]

        # we expect more than one flush since it is also called on shutdown
        assert len(received) > 1

        # expect all metrics in default set are received
        # DEV: dogstatsd gauges in form "{metric_name}:{metric_value}|g#t{tag_name}:{tag_value},..."
        assert (
            DEFAULT_RUNTIME_METRICS & set([gauge.split(":")[0] for packet in received for gauge in packet.split("\n")])
            == DEFAULT_RUNTIME_METRICS
        )

        # check to last set of metrics returned to confirm tags were set
        for gauge in received[-1:]:
            self.assertRegexpMatches(gauge, "service:parent")
            self.assertRegexpMatches(gauge, "service:child")
            self.assertNotRegexpMatches(gauge, "service:db")
            self.assertRegexpMatches(gauge, "env:tests.dog")
            self.assertRegexpMatches(gauge, "lang_interpreter:CPython")
            self.assertRegexpMatches(gauge, "lang_version:")
            self.assertRegexpMatches(gauge, "lang:python")
            self.assertRegexpMatches(gauge, "tracer_version:")
