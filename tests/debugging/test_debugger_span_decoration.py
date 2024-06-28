# -*- encoding: utf-8 -*-
import sys

import ddtrace
from ddtrace.debugging._probe.model import ProbeEvaluateTimingForMethod
from ddtrace.debugging._probe.model import SpanDecoration
from ddtrace.debugging._probe.model import SpanDecorationTag
from ddtrace.debugging._probe.model import SpanDecorationTargetSpan
from ddtrace.debugging._signal.model import EvaluationError
from tests.debugging.mocking import debugger
from tests.debugging.utils import create_span_decoration_function_probe
from tests.debugging.utils import create_span_decoration_line_probe
from tests.debugging.utils import ddexpr
from tests.debugging.utils import ddstrtempl
from tests.utils import TracerTestCase


class SpanDecorationProbeTestCase(TracerTestCase):
    def setUp(self):
        super(SpanDecorationProbeTestCase, self).setUp()

        import tests.submod.traced_stuff as ts

        self.traced_stuff = ts
        self.backup_tracer = ddtrace.tracer
        self.old_traceme = ts.traceme

        ddtrace.tracer = self.tracer

        ts.traceme = self.tracer.wrap(name="traceme", service="test")(ts.traceme)

    def tearDown(self):
        del sys.modules["tests.submod.traced_stuff"]
        ddtrace.tracer = self.backup_tracer
        super(SpanDecorationProbeTestCase, self).tearDown()

    def test_debugger_span_decoration_probe_on_inner_function_active_span(self):
        with debugger() as d:
            d.add_probes(
                create_span_decoration_function_probe(
                    probe_id="span-decoration",
                    module="tests.submod.traced_stuff",
                    func_qname="inner",
                    evaluate_at=ProbeEvaluateTimingForMethod.EXIT,
                    target_span=SpanDecorationTargetSpan.ACTIVE,
                    decorations=[
                        SpanDecoration(
                            when=ddexpr(True),
                            tags=[
                                SpanDecorationTag(name="test.tag", value=ddstrtempl([{"ref": "@return"}])),
                                SpanDecorationTag(name="test.bad", value=ddstrtempl([{"ref": "notathing"}])),
                            ],
                        )
                    ],
                )
            )

            assert self.traced_stuff.traceme() == 42 << 1

            self.assert_span_count(1)
            (span,) = self.get_spans()

            assert span.name == "traceme"
            assert span.get_tag("test.tag") == "42"
            assert span.get_tag("_dd.di.test.tag.probe_id") == "span-decoration"
            assert (
                span.get_tag("_dd.di.test.bad.evaluation_error")
                == "'Failed to evaluate expression \"test\": \\'notathing\\''"
            )

            assert not d.test_queue

    def test_debugger_span_decoration_probe_on_inner_function_active_span_unconditional_and_bad_condition(self):
        with debugger() as d:
            d.add_probes(
                create_span_decoration_function_probe(
                    probe_id="span-decoration",
                    module="tests.submod.traced_stuff",
                    func_qname="inner",
                    evaluate_at=ProbeEvaluateTimingForMethod.EXIT,
                    target_span=SpanDecorationTargetSpan.ACTIVE,
                    decorations=[
                        SpanDecoration(
                            when=None,
                            tags=[
                                SpanDecorationTag(name="test.tag", value=ddstrtempl([{"ref": "@return"}])),
                                SpanDecorationTag(name="test.bad", value=ddstrtempl([{"ref": "notathing"}])),
                            ],
                        ),
                        SpanDecoration(
                            when=ddexpr({"ref": "notathing"}),
                            tags=[
                                SpanDecorationTag(name="test.failedcond", value=ddstrtempl([{"ref": "@return"}])),
                            ],
                        ),
                    ],
                )
            )

            assert self.traced_stuff.traceme() == 42 << 1

            self.assert_span_count(1)
            (span,) = self.get_spans()

            assert span.name == "traceme"
            assert int(span.get_tag("test.tag"))
            assert span.get_tag("_dd.di.test.tag.probe_id") == "span-decoration"
            assert (
                span.get_tag("_dd.di.test.bad.evaluation_error")
                == "'Failed to evaluate expression \"test\": \\'notathing\\''"
            )

            (signal,) = d.test_queue
            assert signal.errors == [EvaluationError(expr="test", message="Failed to evaluate condition: 'notathing'")]

            (payload,) = d.uploader.wait_for_payloads()
            assert payload["message"] == "Condition evaluation errors for probe span-decoration"

    def test_debugger_span_decoration_probe_in_inner_function_active_span(self):
        with debugger() as d:
            d.add_probes(
                create_span_decoration_line_probe(
                    probe_id="span-decoration",
                    source_file="tests/submod/traced_stuff.py",
                    line=3,
                    target_span=SpanDecorationTargetSpan.ACTIVE,
                    decorations=[
                        SpanDecoration(
                            when=ddexpr(True),
                            tags=[SpanDecorationTag(name="test.tag", value=ddstrtempl(["test.value"]))],
                        )
                    ],
                )
            )

            assert self.traced_stuff.traceme() == 42 << 1

            self.assert_span_count(1)
            (span,) = self.get_spans()

            assert span.name == "traceme"
            assert span.get_tag("test.tag") == "test.value"
            assert span.get_tag("_dd.di.test.tag.probe_id") == "span-decoration"

    def test_debugger_span_decoration_probe_on_traced_function_active_span(self):
        with debugger() as d:
            d.add_probes(
                create_span_decoration_function_probe(
                    probe_id="span-decoration",
                    module="tests.submod.traced_stuff",
                    func_qname="traceme",
                    evaluate_at=ProbeEvaluateTimingForMethod.ENTER,
                    target_span=SpanDecorationTargetSpan.ACTIVE,
                    decorations=[
                        SpanDecoration(
                            when=ddexpr(True),
                            tags=[SpanDecorationTag(name="test.tag", value=ddstrtempl(["test.value"]))],
                        )
                    ],
                )
            )

            assert self.traced_stuff.traceme() == 42 << 1

            self.assert_span_count(1)
            (span,) = self.get_spans()

            assert span.name == "traceme"
            assert span.get_tag("test.tag") == "test.value"
            assert span.get_tag("_dd.di.test.tag.probe_id") == "span-decoration"

    def test_debugger_span_decoration_probe_in_traced_function_active_span(self):
        with debugger() as d:
            d.add_probes(
                create_span_decoration_line_probe(
                    probe_id="span-decoration",
                    source_file="tests/submod/traced_stuff.py",
                    line=7,
                    target_span=SpanDecorationTargetSpan.ACTIVE,
                    decorations=[
                        SpanDecoration(
                            when=ddexpr(True),
                            tags=[SpanDecorationTag(name="test.tag", value=ddstrtempl(["test.value"]))],
                        )
                    ],
                )
            )

            assert self.traced_stuff.traceme() == 42 << 1

            self.assert_span_count(1)
            (span,) = self.get_spans()

            assert span.name == "traceme"
            assert span.get_tag("test.tag") == "test.value"
            assert span.get_tag("_dd.di.test.tag.probe_id") == "span-decoration"

    def test_debugger_span_decoration_probe_in_traced_function_root_span(self):
        with debugger() as d:
            d.add_probes(
                create_span_decoration_line_probe(
                    probe_id="span-decoration",
                    source_file="tests/submod/traced_stuff.py",
                    line=8,
                    target_span=SpanDecorationTargetSpan.ROOT,
                    decorations=[
                        SpanDecoration(
                            when=ddexpr(True),
                            tags=[SpanDecorationTag(name="test.tag", value=ddstrtempl([{"ref": "cake"}]))],
                        )
                    ],
                )
            )

            with self.tracer.trace("root") as root:
                assert self.traced_stuff.traceme() == 42 << 1

            self.assert_span_count(2)

            parent, child = self.get_spans()

            assert parent is root
            assert parent.get_tag("test.tag") == "🍰"
            assert parent.get_tag("_dd.di.test.tag.probe_id") == "span-decoration"

            assert child.name == "traceme"
            assert child.get_tag("test.tag") is None
            assert child.get_tag("_dd.di.test.tag.probe_id") is None
