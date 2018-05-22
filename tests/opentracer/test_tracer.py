import pytest

from ddtrace.opentracer import Tracer


class TestTracerConfig(object):
    def test_config(self):
        """Test the configuration of the tracer"""
        config = {
            'enabled': True,
            'service_name': 'myservice'
        }
        tracer = Tracer(config=config)

        assert tracer._service_name == 'myservice'
        assert tracer._enabled is True

    def test_no_service_name(self):
        """Config without a service_name should raise an exception."""
        from ddtrace.settings import ConfigException

        with pytest.raises(ConfigException):
            tracer = Tracer()
            assert tracer is not None


class TestTracer(object):
    def test_init(self):
        """Very basic test for skeleton code"""
        tracer = Tracer(service_name='myservice')
        assert tracer is not None

