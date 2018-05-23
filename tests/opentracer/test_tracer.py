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

    def test_multiple_tracer_configs(self):
        """Ensure that a tracer config is a copy of the passed config."""
        config = {
            'service_name': 'serv1',
        }

        tracer1 = Tracer(config=config)
        assert tracer1._service_name == 'serv1'

        config['service_name'] = 'serv2'
        tracer2 = Tracer(config=config)

        # Ensure tracer1's config was not mutated
        assert tracer1._service_name == 'serv1'
        assert tracer2._service_name == 'serv2'

    def test_invalid_config_key(self):
        """A config with an invalid key should raise a ConfigException."""
        from ddtrace.settings import ConfigException
        config = {
            'service_name': 'myservice',
            'enabeld': False,
        }

        # No debug flag should not raise an error
        tracer = Tracer(config=config)
        assert tracer is not None

        # With debug flag should raise an error
        config['debug'] = True
        with pytest.raises(ConfigException):
            tracer = Tracer(config=config)
            assert tracer is not None


class TestTracer(object):
    def test_init(self):
        """Very basic test for skeleton code"""
        tracer = Tracer(service_name='myservice')
        assert tracer is not None

