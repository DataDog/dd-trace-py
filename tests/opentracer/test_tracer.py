import pytest

from ddtrace.opentracer import Tracer


def get_dummy_ot_tracer(service_name='', config=None, scope_manager=None):
    from ..test_tracer import get_dummy_tracer
    config = config or {}
    tracer = Tracer(service_name=service_name, config=config, scope_manager=scope_manager)
    tracer._dd_tracer = get_dummy_tracer()
    return tracer


@pytest.fixture
def nop_tracer():
    return get_dummy_ot_tracer(service_name='mysvc')


# helper to get the spans from a nop_tracer
def get_spans(tracer):
    return tracer._dd_tracer.writer.pop()


class TestTracerConfig(object):
    def test_config(self):
        """Test the configuration of the tracer"""
        config = {
            'enabled': True,
        }
        tracer = Tracer(service_name='myservice', config=config)

        assert tracer._service_name == 'myservice'
        assert tracer._enabled is True

    def test_no_service_name(self):
        """A service_name should be generated if one is not provided."""
        tracer = Tracer()
        assert tracer._service_name

    def test_multiple_tracer_configs(self):
        """Ensure that a tracer config is a copy of the passed config."""
        config = {
            'enabled': True
        }

        tracer1 = Tracer(service_name='serv1', config=config)
        assert tracer1._service_name == 'serv1'

        config['enabled'] = False
        tracer2 = Tracer(service_name='serv2', config=config)

        # Ensure tracer1's config was not mutated
        assert tracer1._service_name == 'serv1'
        assert tracer1._enabled is True

        assert tracer2._service_name == 'serv2'
        assert tracer2._enabled is False

    def test_invalid_config_key(self):
        """A config with an invalid key should raise a ConfigException."""
        from ddtrace.settings import ConfigException
        config = {
            'enabeld': False,
        }

        # No debug flag should not raise an error
        tracer = Tracer(service_name='mysvc', config=config)

        # With debug flag should raise an error
        config['debug'] = True
        with pytest.raises(ConfigException) as ce_info:
            tracer = Tracer(config=config)
            assert 'enabeld' in str(ce_info)
            assert tracer is not None

        # Test with multiple incorrect keys
        config['setttings'] = {}
        with pytest.raises(ConfigException) as ce_info:
            tracer = Tracer(service_name='mysvc', config=config)
            assert ['enabeld', 'setttings'] in str(ce_info)
            assert tracer is not None


class TestTracer(object):
    def test_start_span(self, nop_tracer):
        """Start and finish a span."""
        import time
        with nop_tracer.start_span('myop') as span:
            time.sleep(0.005)

        # span should be finished when the context manager exits
        assert span._finished

        spans = get_spans(nop_tracer)
        assert len(spans) == 1

    def test_start_span_references(self, nop_tracer):
        """Start a span using references."""
        from opentracing import child_of

        with nop_tracer.start_span('one', references=[child_of()]):
            pass

        spans = get_spans(nop_tracer)
        assert spans[0].parent_id is None

        root = nop_tracer.start_active_span('root')
        # create a child using a parent reference that is not the context parent
        with nop_tracer.start_active_span('one'):
            with nop_tracer.start_active_span('two', references=[child_of(root.span)]):
                pass
        root.close()

        spans = get_spans(nop_tracer)
        assert spans[2].parent_id is spans[0].span_id

    def test_start_span_custom_start_time(self, nop_tracer):
        """Start a span with a custom start time."""
        import time
        t = time.time() + 0.002
        with nop_tracer.start_span('myop', start_time=t) as span:
            time.sleep(0.005)

        # it should be certain that the span duration is strictly less than
        # the amount of time we sleep for
        assert span._dd_span.duration < 0.005

    def test_start_span_with_spancontext(self, nop_tracer):
        """Start and finish a span using a span context as the child_of
        reference.
        """
        import time
        with nop_tracer.start_span('myop') as span:
            time.sleep(0.005)
            with nop_tracer.start_span('myop', child_of=span.context) as span2:
                time.sleep(0.008)

        # span should be finished when the context manager exits
        assert span._finished
        assert span2._finished

        spans = get_spans(nop_tracer)
        assert len(spans) == 2

        # ensure proper parenting
        assert spans[1].parent_id is spans[0].span_id

    def test_start_span_with_tags(self, nop_tracer):
        """Create a span with initial tags."""
        tags = {
            'key': 'value',
            'key2': 'value2',
        }
        with nop_tracer.start_span('myop', tags=tags) as span:
            pass

        assert span._dd_span.get_tag('key') == 'value'
        assert span._dd_span.get_tag('key2') == 'value2'

    def test_start_active_span_multi_child(self, nop_tracer):
        """Start and finish multiple child spans.
        This should ensure that child spans can be created 2 levels deep.
        """
        import time
        with nop_tracer.start_active_span('myfirstop') as scope1:
            time.sleep(0.009)
            with nop_tracer.start_active_span('mysecondop') as scope2:
                time.sleep(0.007)
                with nop_tracer.start_active_span('mythirdop') as scope3:
                    time.sleep(0.005)

        # spans should be finished when the context manager exits
        assert scope1.span._finished
        assert scope2.span._finished
        assert scope3.span._finished

        spans = get_spans(nop_tracer)

        # check spans are captured in the trace
        assert scope1.span._dd_span is spans[0]
        assert scope2.span._dd_span is spans[1]
        assert scope3.span._dd_span is spans[2]

        # ensure proper parenting
        assert spans[1].parent_id is spans[0].span_id
        assert spans[2].parent_id is spans[1].span_id

        # sanity check a lower bound on the durations
        assert spans[0].duration >= 0.009 + 0.007 + 0.005
        assert spans[1].duration >= 0.007 + 0.005
        assert spans[2].duration >= 0.005

    def test_start_active_span_multi_child_siblings(self, nop_tracer):
        """Start and finish multiple span at the same level.
        This should test to ensure a parent can have multiple child spans at the
        same level.
        """
        import time
        with nop_tracer.start_active_span('myfirstop') as scope1:
            time.sleep(0.009)
            with nop_tracer.start_active_span('mysecondop') as scope2:
                time.sleep(0.007)
            with nop_tracer.start_active_span('mythirdop') as scope3:
                time.sleep(0.005)

        # spans should be finished when the context manager exits
        assert scope1.span._finished
        assert scope2.span._finished
        assert scope3.span._finished

        spans = get_spans(nop_tracer)

        # check spans are captured in the trace
        assert scope1.span._dd_span is spans[0]
        assert scope2.span._dd_span is spans[1]
        assert scope3.span._dd_span is spans[2]

        # ensure proper parenting
        assert spans[1].parent_id is spans[0].span_id
        assert spans[2].parent_id is spans[0].span_id

        # sanity check a lower bound on the durations
        assert spans[0].duration >= 0.009 + 0.007 + 0.005
        assert spans[1].duration >= 0.007
        assert spans[2].duration >= 0.005

    def test_start_span_manual_child_of(self, nop_tracer):
        """Start spans without using a scope manager.
        Spans should be created without parents since there will be no call
        for the active span.
        """
        import time

        root = nop_tracer.start_span('zero')

        with nop_tracer.start_span('one', child_of=root):
            time.sleep(0.009)
            with nop_tracer.start_span('two', child_of=root):
                time.sleep(0.007)
                with nop_tracer.start_span('three', child_of=root):
                    time.sleep(0.005)
        root.finish()

        spans = get_spans(nop_tracer)

        assert spans[0].parent_id is None
        # ensure each child span is a child of root
        assert spans[1].parent_id is root._dd_span.span_id
        assert spans[2].parent_id is root._dd_span.span_id
        assert spans[3].parent_id is root._dd_span.span_id
        assert spans[0].trace_id == spans[1].trace_id and \
            spans[1].trace_id == spans[2].trace_id

    def test_start_span_no_active_span(self, nop_tracer):
        """Start spans without using a scope manager.
        Spans should be created without parents since there will be no call
        for the active span.
        """
        import time
        with nop_tracer.start_span('one', ignore_active_span=True) as span1:
            time.sleep(0.009)
            with nop_tracer.start_span('two', ignore_active_span=True) as span2:
                time.sleep(0.007)
            with nop_tracer.start_span('three', ignore_active_span=True) as span3:
                time.sleep(0.005)

        spans = get_spans(nop_tracer)

        # ensure each span does not have a parent
        assert spans[0].parent_id is None
        assert spans[1].parent_id is None
        assert spans[2].parent_id is None
        # and that each span is a new trace
        assert spans[0].trace_id != spans[1].trace_id and \
               spans[1].trace_id != spans[2].trace_id and \
               spans[0].trace_id != spans[2].trace_id

    def test_start_active_span_child_finish_after_parent(self, nop_tracer):
        """Start a child span and finish it after its parent."""
        import time

        span1 = nop_tracer.start_active_span('one').span
        span2 = nop_tracer.start_active_span('two').span
        span1.finish()
        time.sleep(0.005)
        span2.finish()

        spans = get_spans(nop_tracer)
        assert len(spans) is 2
        assert spans[0].parent_id is None
        assert spans[1].parent_id is span1._dd_span.span_id
        assert spans[1].duration > spans[0].duration

    def test_start_span_multi_intertwined(self, nop_tracer):
        """Start multiple spans at the top level intertwined.
        Alternate calling between two traces.
        """
        import threading
        import time

        def trace_one():
            id = 11
            with nop_tracer.start_active_span(str(id)):
                id += 1
                time.sleep(0.009)
                with nop_tracer.start_active_span(str(id)):
                    id += 1
                    time.sleep(0.001)
                    with nop_tracer.start_active_span(str(id)):
                        pass

        def trace_two():
            id = 21
            with nop_tracer.start_active_span(str(id)):
                id += 1
                time.sleep(0.006)
                with nop_tracer.start_active_span(str(id)):
                    id += 1
                    time.sleep(0.009)
                with nop_tracer.start_active_span(str(id)):
                    pass

        # the ordering should be
        # t1.span1/t2.span1, t2.span2, t1.span2, t1.span3, t2.span3
        t1 = threading.Thread(target=trace_one)
        t1.daemon = True
        t2 = threading.Thread(target=trace_two)
        t2.daemon = True

        t1.start()
        t2.start()
        # wait for threads to finish
        time.sleep(0.018)

        spans = get_spans(nop_tracer)

        # trace_one will finish before trace_two so its spans should be written
        # before the spans from trace_two, let's confirm this
        assert spans[0].name == '11'
        assert spans[1].name == '12'
        assert spans[2].name == '13'
        assert spans[3].name == '21'
        assert spans[4].name == '22'
        assert spans[5].name == '23'

        # next let's ensure that each span has the correct parent:
        # trace_one
        assert spans[0].parent_id is None
        assert spans[1].parent_id is spans[0].span_id
        assert spans[2].parent_id is spans[1].span_id
        # trace_two
        assert spans[3].parent_id is None
        assert spans[4].parent_id is spans[3].span_id
        assert spans[5].parent_id is spans[3].span_id

        # finally we should ensure that the trace_ids are reasonable
        # trace_one
        assert spans[0].trace_id == spans[1].trace_id and \
            spans[1].trace_id == spans[2].trace_id
        # traces should be independent
        assert spans[2].trace_id != spans[3].trace_id
        # trace_two
        assert spans[3].trace_id == spans[4].trace_id and \
            spans[4].trace_id == spans[5].trace_id

    def test_start_active_span(self, nop_tracer):
        with nop_tracer.start_active_span('one') as scope:
            pass

        assert scope.span._dd_span.name == 'one'
        assert scope.span._finished
        spans = get_spans(nop_tracer)
        assert spans

    def test_start_active_span_finish_on_close(self, nop_tracer):
        with nop_tracer.start_active_span('one', finish_on_close=False) as scope:
            pass

        assert scope.span._dd_span.name == 'one'
        assert not scope.span._finished
        spans = get_spans(nop_tracer)
        assert not spans

    def test_start_active_span_nested(self, nop_tracer):
        """Test the active span of multiple nested calls of start_active_span."""
        with nop_tracer.start_active_span('one') as outer_scope:
            assert nop_tracer.active_span == outer_scope.span
            with nop_tracer.start_active_span('two') as inner_scope:
                assert nop_tracer.active_span == inner_scope.span
                with nop_tracer.start_active_span('three') as innest_scope: # why isn't it innest? innermost so verbose
                    assert nop_tracer.active_span == innest_scope.span
            with nop_tracer.start_active_span('two') as inner_scope:
                assert nop_tracer.active_span == inner_scope.span
            assert nop_tracer.active_span == outer_scope.span
        assert nop_tracer.active_span is None

    def test_start_active_span_trace(self, nop_tracer):
        """Test the active span of multiple nested calls of start_active_span."""
        with nop_tracer.start_active_span('one') as outer_scope:
            outer_scope.span.set_tag('outer', 2)
            with nop_tracer.start_active_span('two') as inner_scope:
                inner_scope.span.set_tag('inner', 3)
            with nop_tracer.start_active_span('two') as inner_scope:
                inner_scope.span.set_tag('inner', 3)
                with nop_tracer.start_active_span('three') as innest_scope:
                    innest_scope.span.set_tag('innerest', 4)

        spans = get_spans(nop_tracer)

        assert spans[0].parent_id is None
        assert spans[1].parent_id is spans[0].span_id
        assert spans[2].parent_id is spans[0].span_id
        assert spans[3].parent_id is spans[2].span_id
@pytest.fixture
def nop_span_ctx():
    from ddtrace.ext.priority import AUTO_KEEP
    from ddtrace.opentracer.span_context import SpanContext
    return SpanContext(sampling_priority=AUTO_KEEP, sampled=True)


class TestTracerSpanContextPropagation(object):
    """Test the injection and extration of a span context from a tracer."""

    def test_invalid_format(self, nop_tracer, nop_span_ctx):
        """An invalid format should raise an UnsupportedFormatException."""
        from opentracing import UnsupportedFormatException

        # test inject
        with pytest.raises(UnsupportedFormatException):
            nop_tracer.inject(nop_span_ctx, None, {})

        # test extract
        with pytest.raises(UnsupportedFormatException):
            nop_tracer.extract(None, {})

    def test_inject_invalid_carrier(self, nop_tracer, nop_span_ctx):
        """Only dicts should be supported as a carrier."""
        from opentracing import InvalidCarrierException
        from opentracing import Format

        with pytest.raises(InvalidCarrierException):
            nop_tracer.inject(nop_span_ctx, Format.HTTP_HEADERS, None)

    def test_extract_invalid_carrier(self, nop_tracer):
        """Only dicts should be supported as a carrier."""
        from opentracing import InvalidCarrierException
        from opentracing import Format

        with pytest.raises(InvalidCarrierException):
            nop_tracer.extract(Format.HTTP_HEADERS, None)

    def test_http_headers_base(self, nop_tracer):
        """extract should undo inject for http headers."""
        from opentracing import Format
        from ddtrace.opentracer.span_context import SpanContext

        span_ctx = SpanContext(trace_id=123, span_id=456,)
        carrier = {}

        nop_tracer.inject(span_ctx, Format.HTTP_HEADERS, carrier)
        assert len(carrier.keys()) > 0

        ext_span_ctx = nop_tracer.extract(Format.HTTP_HEADERS, carrier)
        assert ext_span_ctx._dd_context.trace_id == 123
        assert ext_span_ctx._dd_context.span_id == 456

    def test_http_headers_baggage(self, nop_tracer):
        """extract should undo inject for http headers."""
        from opentracing import Format
        from ddtrace.opentracer.span_context import SpanContext

        span_ctx = SpanContext(trace_id=123, span_id=456, baggage={
            'test': 4,
            'test2': 'string',
        })
        carrier = {}

        nop_tracer.inject(span_ctx, Format.HTTP_HEADERS, carrier)
        assert len(carrier.keys()) > 0

        ext_span_ctx = nop_tracer.extract(Format.HTTP_HEADERS, carrier)
        assert ext_span_ctx._dd_context.trace_id == 123
        assert ext_span_ctx._dd_context.span_id == 456
        assert ext_span_ctx.baggage == span_ctx.baggage

    def test_text(self, nop_tracer):
        """extract should undo inject for http headers"""
        from opentracing import Format
        from ddtrace.opentracer.span_context import SpanContext

        span_ctx = SpanContext(trace_id=123, span_id=456, baggage={
            'test': 4,
            'test2': 'string',
        })
        carrier = {}

        nop_tracer.inject(span_ctx, Format.TEXT_MAP, carrier)
        assert len(carrier.keys()) > 0

        ext_span_ctx = nop_tracer.extract(Format.TEXT_MAP, carrier)
        assert ext_span_ctx._dd_context.trace_id == 123
        assert ext_span_ctx._dd_context.span_id == 456
        assert ext_span_ctx.baggage == span_ctx.baggage

    def test_invalid_baggage_key(self, nop_tracer):
        """Invaid baggage keys should be ignored."""
        from opentracing import Format
        from ddtrace.propagation.http import HTTP_HEADER_TRACE_ID
        from ddtrace.opentracer.span_context import SpanContext

        span_ctx = SpanContext(trace_id=123, span_id=456, baggage={
            'test': 4,
            'test2': 'string',
        })
        carrier = {}

        nop_tracer.inject(span_ctx, Format.TEXT_MAP, carrier)
        assert len(carrier.keys()) > 0

        # manually alter a key in the carrier baggage
        del carrier[HTTP_HEADER_TRACE_ID]
        corrupted_key = HTTP_HEADER_TRACE_ID[2:]
        carrier[corrupted_key] = 123

        ext_span_ctx = nop_tracer.extract(Format.TEXT_MAP, carrier)
        assert ext_span_ctx.baggage == span_ctx.baggage

    def test_immutable_span_context(self, nop_tracer):
        """Span contexts should be immutable."""
        with nop_tracer.start_span('root') as root:
            ctx_before = root.context
            root.set_baggage_item('test', 2)
            assert ctx_before is not root.context
            with nop_tracer.start_span('child') as level1:
                with nop_tracer.start_span('child') as level2:
                    pass
        assert root.context is not level1.context
        assert level2.context is not level1.context
        assert level2.context is not root.context

    def test_inherited_baggage(self, nop_tracer):
        """Baggage should be inherited by child spans."""
        with nop_tracer.start_active_span('root') as root:
            # this should be passed down to the child
            root.span.set_baggage_item('root', 1)
            root.span.set_baggage_item('root2', 1)
            with nop_tracer.start_active_span('child') as level1:
                level1.span.set_baggage_item('level1', 1)
                with nop_tracer.start_active_span('child') as level2:
                    level2.span.set_baggage_item('level2', 1)
        # ensure immutability
        assert level1.span.context is not root.span.context
        assert level2.span.context is not level1.span.context

        # level1 should have inherited the baggage of root
        assert level1.span.get_baggage_item('root')
        assert level1.span.get_baggage_item('root2')

        # level2 should have inherited the baggage of both level1 and level2
        assert level2.span.get_baggage_item('root')
        assert level2.span.get_baggage_item('root2')
        assert level2.span.get_baggage_item('level1')
        assert level2.span.get_baggage_item('level2')


class TestTracerCompatibility(object):
    """Ensure that our opentracer produces results in the underlying datadog tracer."""

    def test_required_dd_fields(self):
        """Ensure required fields needed for successful tracing are possessed
        by the underlying datadog tracer.
        """
        # a service name is required
        tracer = Tracer('service')
        with tracer.start_span('my_span') as span:
            assert span._dd_span.service
