import contextlib
import os
import time

import mock
import pytest

import ddtrace
from ddtrace.ext import SpanTypes
from ddtrace.internal.runtime.constants import DEFAULT_RUNTIME_METRICS
from ddtrace.internal.runtime.constants import ENV
from ddtrace.internal.runtime.constants import GC_COUNT_GEN0
from ddtrace.internal.runtime.constants import SERVICE
from ddtrace.internal.runtime.runtime_metrics import RuntimeMetrics
from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
from ddtrace.internal.runtime.runtime_metrics import TracerTags
from ddtrace.internal.service import ServiceStatus
from tests.utils import BaseTestCase
from tests.utils import TracerTestCase
from tests.utils import call_program


@contextlib.contextmanager
def runtime_metrics_service(tracer=None):
    RuntimeWorker.enable()
    assert RuntimeWorker._instance is not None
    assert RuntimeWorker._instance.status == ServiceStatus.RUNNING

    yield RuntimeWorker._instance

    RuntimeWorker._instance.stop()
    assert RuntimeWorker._instance.status == ServiceStatus.STOPPED
    RuntimeWorker._instance = None


class TestRuntimeTags(TracerTestCase):
    def test_all_tags(self):
        with self.override_global_tracer():
            with self.trace("test", service="test"):
                tags = set([k for (k, v) in TracerTags()])
                assert SERVICE in tags
                # no env set by default
                assert ENV not in tags

    def test_one_tag(self):
        with self.override_global_tracer():
            with self.trace("test", service="test"):
                tags = [k for (k, v) in TracerTags(enabled=[SERVICE])]
                self.assertEqual(set(tags), set([SERVICE]))

    def test_env_tag(self):
        def filter_only_env_tags(tags):
            return [(k, v) for (k, v) in TracerTags() if k == "env"]

        with self.override_global_tracer():
            # first without env tag set in tracer
            with self.trace("first-test", service="test"):
                tags = filter_only_env_tags(TracerTags())
                assert tags == []

            # then with an env tag set
            self.tracer.set_tags({"env": "tests.dog"})
            with self.trace("second-test", service="test"):
                tags = filter_only_env_tags(TracerTags())
                assert tags == [("env", "tests.dog")]

            # check whether updating env works
            self.tracer.set_tags({"env": "staging.dog"})
            with self.trace("third-test", service="test"):
                tags = filter_only_env_tags(TracerTags())
                assert tags == [("env", "staging.dog")]


@pytest.mark.subprocess(env={})
def test_runtime_tags_empty():
    from ddtrace.internal.runtime.runtime_metrics import PlatformTags

    tags = list(PlatformTags())
    assert len(tags) == 4

    tags = dict(tags)
    assert set(tags.keys()) == set(["lang", "lang_interpreter", "lang_version", "tracer_version"])


@pytest.mark.subprocess()
def test_runtime_platformv2_tags():
    from ddtrace.internal.runtime.runtime_metrics import PlatformTagsV2

    tags = list(PlatformTagsV2())
    assert len(tags) == 5

    tags = dict(tags)
    # Ensure runtime-id is present along with all the v1 tags
    assert set(tags.keys()) == set(["lang", "lang_interpreter", "lang_version", "tracer_version", "runtime-id"])


@pytest.mark.subprocess(env={"DD_SERVICE": "my-service", "DD_ENV": "test-env", "DD_VERSION": "1.2.3"})
def test_runtime_tags_usm():
    from ddtrace.internal.runtime.runtime_metrics import TracerTags

    tags = list(TracerTags())
    assert len(tags) == 3, tags

    tags = dict(tags)
    assert set(tags.keys()) == set(["service", "version", "env"])
    assert tags["service"] == "my-service"
    assert tags["env"] == "test-env"
    assert tags["version"] == "1.2.3"


@pytest.mark.subprocess(env={"DD_TAGS": "version:1.2.3,custom:tag,test:key", "DD_VERSION": "4.5.6"})
def test_runtime_tags_dd_tags():
    from ddtrace.internal.runtime.runtime_metrics import TracerTags

    tags = list(TracerTags())
    assert len(tags) == 4, tags

    tags = dict(tags)
    assert set(tags.keys()) == set(["version", "custom", "test", "service"])
    assert tags["custom"] == "tag"
    assert tags["test"] == "key"
    assert tags["version"] == "4.5.6"


@pytest.mark.subprocess()
def test_runtime_tags_manual_tracer_tags():
    from ddtrace.internal.runtime.runtime_metrics import TracerTags
    from ddtrace.trace import tracer

    tracer.set_tags({"manual": "tag"})

    tags = list(TracerTags())
    assert len(tags) == 2, tags

    tags = dict(tags)
    assert set(tags.keys()) == set(["manual", "service"])
    assert tags["manual"] == "tag"


def test_runtime_worker_flush_preserves_entity_id_tag(monkeypatch):
    # Regression test for GH-19526: flush() used to overwrite the dogstatsd client's
    # constant_tags wholesale, permanently dropping dd.internal.entity_id (derived from
    # DD_ENTITY_ID at client construction) after the very first flush. Assert against the
    # packets actually sent on the wire, not just the client's in-memory tag list.
    monkeypatch.setenv("DD_ENTITY_ID", "test-entity-123")

    with mock.patch("socket.socket") as sock:
        sock.return_value.getsockopt.return_value = 0
        worker = RuntimeWorker()
        assert "dd.internal.entity_id:test-entity-123" in worker._dogstatsd_client.constant_tags

        for _ in range(3):
            worker.flush()

        statsd_socket = worker._dogstatsd_client.socket
        received = [s.args[0].decode("utf-8") for s in statsd_socket.send.mock_calls]
        assert received, "expected at least one packet to be sent"
        gauges = [line for packet in received for line in packet.split("\n") if line]
        assert gauges, "expected at least one metric line to be sent"
        for gauge in gauges:
            assert gauge.count("dd.internal.entity_id:test-entity-123") == 1, gauge


def test_runtime_worker_flush_dedupes_entity_id_tag(monkeypatch):
    # If a customer applied the documented DD_TAGS workaround before this fix shipped, the same
    # entity_id tag now arrives via both the client's constant_tags snapshot and TracerTagCollector
    # (which surfaces DD_TAGS). flush() should dedupe the exact-string repeat in the outgoing
    # packets rather than emit it twice.
    monkeypatch.setenv("DD_ENTITY_ID", "test-entity-123")
    monkeypatch.setenv("DD_TAGS", "dd.internal.entity_id:test-entity-123")

    with mock.patch("socket.socket") as sock:
        sock.return_value.getsockopt.return_value = 0
        worker = RuntimeWorker()
        worker.flush()

        statsd_socket = worker._dogstatsd_client.socket
        received = [s.args[0].decode("utf-8") for s in statsd_socket.send.mock_calls]
        assert received, "expected at least one packet to be sent"
        gauges = [line for packet in received for line in packet.split("\n") if line]
        assert gauges, "expected at least one metric line to be sent"
        for gauge in gauges:
            assert gauge.count("dd.internal.entity_id:test-entity-123") == 1, gauge


def test_runtime_worker_flush_does_not_leak_stale_service_tag(monkeypatch):
    # The dogstatsd client also derives service/env/version tags from DD_SERVICE/DD_ENV/DD_VERSION
    # at construction. If ddtrace.config.service (etc.) changes afterwards -- e.g. a framework
    # integration setting it once the app has started, after RuntimeWorker already snapshotted the
    # client's tags -- flush() must only send the current value, not both the stale snapshot value
    # and the current one.
    monkeypatch.setenv("DD_SERVICE", "env-service")

    with mock.patch("socket.socket") as sock:
        sock.return_value.getsockopt.return_value = 0
        worker = RuntimeWorker()
        ddtrace.config.service = "override-service"
        try:
            worker.flush()
        finally:
            ddtrace.config.service = None

        statsd_socket = worker._dogstatsd_client.socket
        received = [s.args[0].decode("utf-8") for s in statsd_socket.send.mock_calls]
        assert received, "expected at least one packet to be sent"
        gauges = [line for packet in received for line in packet.split("\n") if line]
        assert gauges, "expected at least one metric line to be sent"
        for gauge in gauges:
            assert "service:env-service" not in gauge, gauge
            assert gauge.count("service:override-service") == 1, gauge


class TestRuntimeMetrics(BaseTestCase):
    def test_all_metrics(self):
        metrics = set([k for (k, v) in RuntimeMetrics()])
        self.assertSetEqual(metrics, DEFAULT_RUNTIME_METRICS)

    def test_one_metric(self):
        metrics = [k for (k, v) in RuntimeMetrics(enabled=[GC_COUNT_GEN0])]
        self.assertEqual(metrics, [GC_COUNT_GEN0])


class TestRuntimeWorker(TracerTestCase):
    @pytest.mark.skip(reason="Very frequently retried")
    def test_tracer_metrics(self):
        # Mock socket.socket to hijack the dogstatsd socket
        with mock.patch("socket.socket") as sock:
            sock.return_value.getsockopt.return_value = 0
            with runtime_metrics_service(tracer=self.tracer):
                self.tracer.set_tags({"env": "tests.dog"})

                with self.override_global_tracer(self.tracer):
                    # spans are started for three services but only web and worker
                    # span types should be included in tags for runtime metrics
                    with self.start_span("parent", service="parent", span_type=SpanTypes.WEB) as root:
                        context = root.context
                        with self.start_span(
                            "child", service="child", span_type=SpanTypes.WORKER, child_of=context
                        ) as child:
                            with self.start_span(
                                "query", service="db", span_type=SpanTypes.SQL, child_of=child.context
                            ):
                                time.sleep(4)
                                # Get the mocked socket for inspection later
                                statsd_socket = RuntimeWorker._instance._dogstatsd_client.socket
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
            self.assertRegex(gauge, "service:tests.tracer")
            self.assertNotIn(gauge, "service:parent")
            self.assertNotIn(gauge, "service:child")
            self.assertNotIn(gauge, "service:db")
            self.assertRegex(gauge, "env:tests.dog")
            self.assertRegex(gauge, "lang_interpreter:CPython")
            self.assertRegex(gauge, "lang_version:")
            self.assertRegex(gauge, "lang:python")
            self.assertRegex(gauge, "tracer_version:")

    def test_root_and_child_span_runtime_internal_span_types(self):
        with runtime_metrics_service(tracer=self.tracer):
            for span_type in ("custom", "template", "web", "worker"):
                with self.start_span("root", span_type=span_type) as root:
                    with self.start_span("child", child_of=root) as child:
                        pass
                assert root.get_tag("language") == "python"
                assert child.get_tag("language") is None

    def test_only_root_span_runtime_external_span_types(self):
        with runtime_metrics_service(tracer=self.tracer):
            for span_type in (
                "algoliasearch.search",
                "boto",
                "cache",
                "cassandra",
                "elasticsearch",
                "grpc",
                "kombu",
                "http",
                "memcached",
                "redis",
                "sql",
                "vertica",
            ):
                with self.start_span("root", span_type=span_type) as root:
                    with self.start_span("child", child_of=root) as child:
                        pass
                assert root.get_tag("language") == "python"
                assert child.get_tag("language") is None


def test_fork():
    _, _, exitcode, _ = call_program("python", os.path.join(os.path.dirname(__file__), "fork_enable.py"))
    assert exitcode == 0

    _, _, exitcode, _ = call_program("python", os.path.join(os.path.dirname(__file__), "fork_disable.py"))
    assert exitcode == 0
