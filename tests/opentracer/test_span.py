import pytest
from ddtrace.opentracer.span import Span, SpanLog


@pytest.fixture
def nop_tracer():
    from ddtrace.opentracer import Tracer
    return Tracer(service_name='mysvc', config={})

@pytest.fixture
def nop_span_ctx():
    from ddtrace.ext.priority import AUTO_KEEP
    from ddtrace.opentracer.span_context import SpanContext
    return SpanContext(sampling_priority=AUTO_KEEP, sampled=True)

@pytest.fixture
def nop_span(nop_tracer, nop_span_ctx):
    return Span(nop_tracer, nop_span_ctx, 'my_op_name')


class TestSpan(object):
    """Test the Datadog OpenTracing Span implementation."""

    def test_init(self, nop_tracer):
        """Very basic test for skeleton code"""
        span = Span(nop_tracer, None, None)
        assert span is not None

    def test_tags(self, nop_span):
        """Set a tag and get it back."""
        nop_span.set_tag('test', 23)
        assert int(nop_span.get_tag('test')) == 23

    def test_set_baggage(self, nop_span):
        """Test setting baggage."""
        r = nop_span.set_baggage_item('test', 23)
        assert r is nop_span

        r = nop_span.set_baggage_item('1', 1).set_baggage_item('2', 2)
        assert r is nop_span

    def test_get_baggage(self, nop_span):
        """Test setting and getting baggage."""
        # test a single item
        nop_span.set_baggage_item('test', 23)
        assert int(nop_span.get_baggage_item('test')) == 23

        # test multiple items
        nop_span.set_baggage_item('1', '1').set_baggage_item('2', 2)
        assert int(nop_span.get_baggage_item('test')) == 23
        assert nop_span.get_baggage_item('1') == '1'
        assert int(nop_span.get_baggage_item('2')) == 2

    def test_log_kv(self, nop_span):
        """Ensure logging values doesn't break anything"""
        # just log a bunch of values
        nop_span.log_kv({'myval': 2})
        nop_span.log_kv({'myval': 3})
        nop_span.log_kv({'myval': 5})


class TestSpanLog():
    def test_init(self):
        log = SpanLog()
        assert len(log) == 0

    def test_add_record(self):
        """Add new records to a log."""
        import time
        log = SpanLog()
        # add a record without a timestamp
        record = {'event': 'now'}
        log.add_record(record)

        # add a record with a timestamp
        log.add_record({'event2': 'later'}, time.time())

        assert len(log) == 2
        assert log[0].record == record
        assert log[0].timestamp <= log[1].timestamp
