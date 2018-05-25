import pytest

from ddtrace.opentracer import Tracer


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
        """Config without a service_name should raise an exception."""
        from ddtrace.settings import ConfigException

        with pytest.raises(ConfigException):
            tracer = Tracer()
            assert tracer is not None

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

@pytest.fixture
def nop_tracer():
    return Tracer(config={'service_name': 'mysvc'})


@pytest.fixture
def nop_span_ctx():
    from ddtrace.ext.priority import AUTO_KEEP
    from ddtrace.opentracer.span_context import SpanContext
    return SpanContext(sampling_priority=AUTO_KEEP, sampled=True)


class TestTracerInjectExtract(object):
    """Test the injection and extration of a span context from a tracer"""

    def test_invalid_format(self, nop_tracer, nop_span_ctx):
        """An invalid format should raise an UnsupportedFormatException."""
        from opentracing import UnsupportedFormatException
        with pytest.raises(UnsupportedFormatException):
            nop_tracer.inject(nop_span_ctx, None, {})

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
        """extract should undo inject for http headers"""
        from opentracing import Format
        from ddtrace.opentracer.span_context import SpanContext

        span_ctx = SpanContext(trace_id=123, span_id=456,)
        carrier = {}

        nop_tracer.inject(span_ctx, Format.HTTP_HEADERS, carrier)
        assert len(carrier.keys()) > 0

        ext_span_ctx = nop_tracer.extract(Format.HTTP_HEADERS, carrier)
        assert ext_span_ctx._context.trace_id == 123
        assert ext_span_ctx._context.span_id == 456

    def test_http_headers_baggage(self, nop_tracer):
        """extract should undo inject for http headers"""
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
        assert ext_span_ctx._context.trace_id == 123
        assert ext_span_ctx._context.span_id == 456
        assert ext_span_ctx.baggage == span_ctx.baggage

<<<<<<< HEAD
=======
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
        assert ext_span_ctx._context.trace_id == 123
        assert ext_span_ctx._context.span_id == 456
        assert ext_span_ctx.baggage == span_ctx.baggage


class TestTracer(object):
    def test_init(self):
        """Very basic test for skeleton code"""
        tracer = Tracer(service_name='myservice')
        assert tracer is not None
>>>>>>> [opentracer] add tests and docs for HTTPPropagator
