# -*- coding: utf-8 -*-
import os

import mock
import pytest

from ddtrace import Tracer
from ddtrace.internal import agent


AGENT_VERSION = os.environ.get("AGENT_VERSION")


class TestTraceAcceptedByAgent:
    def test_simple_trace_accepted_by_agent(self):
        tracer = Tracer()
        with mock.patch("ddtrace.internal.writer.log") as log:
            with tracer.trace("root"):
                for _ in range(999):
                    with tracer.trace("child"):
                        pass
            tracer.shutdown()
        log.warning.assert_not_called()
        log.error.assert_not_called()

    @pytest.mark.parametrize(
        "tags",
        [
            ({"env": "my-env", "tag1": "some_str_1", "tag2": "some_str_2", "tag3": "some_str_3"}),
            ({"env": "test-env", b"tag1": "some_str_1", b"tag2": "some_str_2", b"tag3": "some_str_3"}),
            ({"env": "my-test-env", u"😐": "some_str_1", b"tag2": "some_str_2", "unicode": u"😐"}),
        ],
    )
    def test_trace_with_meta_accepted_by_agent(self, tags):
        """Meta tags should be text types."""
        tracer = Tracer()
        with mock.patch("ddtrace.internal.writer.log") as log:
            with tracer.trace("root", service="test_encoding", resource="test_resource") as root:
                root.set_tags(tags)
                for _ in range(999):
                    with tracer.trace("child") as child:
                        child.set_tags(tags)
            tracer.shutdown()
        log.warning.assert_not_called()
        log.error.assert_not_called()

    @pytest.mark.parametrize(
        "metrics",
        [
            ({"num1": 12345, "num2": 53421, "num3": 1, "num4": 10}),
            ({b"num1": 123.45, b"num2": 543.21, b"num3": 11.0, b"num4": 1.20}),
            ({u"😐": "123.45", b"num2": "1", "num3": "999.99", "num4": "12345"}),
        ],
    )
    def test_trace_with_metrics_accepted_by_agent(self, metrics):
        """Metric tags should be numeric types - i.e. int, float, long (py3), and str numbers."""
        tracer = Tracer()
        with mock.patch("ddtrace.internal.writer.log") as log:
            with tracer.trace("root") as root:
                root.set_metrics(metrics)
                for _ in range(999):
                    with tracer.trace("child") as child:
                        child.set_metrics(metrics)
            tracer.shutdown()
        log.warning.assert_not_called()
        log.error.assert_not_called()


@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="Test agent doesn't return 400 response for bad trace")
class TestTraceRejectedByAgent:
    def _assert_bad_trace_refused_by_agent(self, mock_log):
        """Assert that agent refused a bad trace via log call."""
        calls = [
            mock.call(
                "failed to send traces to Datadog Agent at %s: HTTP error status %s, reason %s",
                agent.get_trace_url(),
                400,
                "Bad Request",
            )
        ]
        mock_log.error.assert_has_calls(calls)

    def test_wrong_span_name_type_refused_by_agent(self):
        """Span names should be a text type."""
        tracer = Tracer()
        with mock.patch("ddtrace.internal.writer.log") as log:
            with tracer.trace(123):
                pass
            tracer.shutdown()

        self._assert_bad_trace_refused_by_agent(log)

    @pytest.mark.parametrize(
        "meta",
        [
            ({"env": "my-env", "tag1": "some_str_1", "tag2": "some_str_2", "tag3": [1, 2, 3]}),
            ({"env": "test-env", b"tag1": {"wrong_type": True}, b"tag2": "some_str_2", b"tag3": "some_str_3"}),
            ({"env": "my-test-env", u"😐": "some_str_1", b"tag2": "some_str_2", "unicode": 12345}),
        ],
    )
    def test_trace_with_wrong_meta_types_refused_by_agent(self, meta):
        tracer = Tracer()
        with mock.patch("ddtrace.internal.writer.log") as log:
            with tracer.trace("root") as root:
                root.meta = meta
                for _ in range(499):
                    with tracer.trace("child") as child:
                        child.meta = meta
            tracer.shutdown()

        self._assert_bad_trace_refused_by_agent(log)

    @pytest.mark.parametrize(
        "metrics",
        [
            ({"num1": 12345, "num2": 53421, "num3": 1, "num4": "not-a-number"}),
            ({b"num1": 123.45, b"num2": [1, 2, 3], b"num3": 11.0, b"num4": 1.20}),
            ({u"😐": "123.45", b"num2": "1", "num3": {"is_number": False}, "num4": "12345"}),
        ],
    )
    def test_trace_with_wrong_metrics_types_refused_by_agent(self, metrics):
        tracer = Tracer()
        with mock.patch("ddtrace.internal.writer.log") as log:
            with tracer.trace("root") as root:
                root.metrics = metrics
                for _ in range(499):
                    with tracer.trace("child") as child:
                        child.metrics = metrics
            tracer.shutdown()

        self._assert_bad_trace_refused_by_agent(log)
