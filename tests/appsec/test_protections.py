from ddtrace import appsec
from tests.utils import TracerTestCase
from tests.utils import override_env


class DummyEventWriter:
    def __init__(self):
        self.events = []

    def write(self, events):
        self.events.extend(events)

    def flush(self, timeout=None):
        pass


class TestSqreenLibrary(TracerTestCase):
    def setUp(self):
        super(TestSqreenLibrary, self).setUp()
        appsec.enable()
        appsec._mgmt.writer = self.writer = DummyEventWriter()

    def test_report(self):
        with self.trace("test") as span:
            appsec.process_request(span, query="foo=bar")
        assert "_dd.sq.process_ms" in span.metrics

        with self.trace("test") as span:
            appsec.process_request(span, headers={"user-agent": "Arachni/v1"})
        assert "_dd.sq.process_ms" in span.metrics
        assert "_dd.sq.reports" in span.metrics

    def test_overtime(self):
        with override_env({"DD_APPSEC_SQREEN_BUDGET_MS": "0"}):
            appsec.enable()

            with self.trace("test") as span:
                appsec.process_request(span, query="foo=bar")
            assert "_dd.sq.process_ms" in span.metrics
            assert span.metrics.get("_dd.sq.overtime_ms") == span.metrics.get("_dd.sq.process_ms")

    def test_attack_event(self):
        with self.trace("test") as span:
            appsec.process_request(span, headers={"user-agent": "Arachni/v1"})
        assert self.writer.events[0].event_type == "appsec.threat.attack"
