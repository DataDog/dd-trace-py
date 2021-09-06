import mock

from ddtrace.appsec.internal.events import context
from tests.utils import TracerTestCase


class AppSecEventsTestCase(TracerTestCase):
    def test_required_context(self):
        with self.trace("test", service="test") as span:
            self.assertEqual(
                context.get_required_context(),
                context.Context_0_1_0(
                    tracer=mock.ANY,
                    trace=context.Trace_0_1_0(span.trace_id),
                    span=context.Span_0_1_0(span.span_id),
                    service=context.Service_0_1_0(name="test"),
                ),
            )
