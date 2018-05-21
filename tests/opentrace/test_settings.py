import opentracing
import pytest
import unittest

from ddtrace.settings import ConfigException
from ddtrace.opentrace import Config


class OpenTraceConfigTest(unittest.TestCase):
    """Test for the opentrace config"""

    open_tracer = None

    def setUp(self):
        OpenTraceConfigTest.open_tracer = opentracing.tracer

    def tearDown(self):
        opentracing.tracer = OpenTraceConfigTest.open_tracer

    def test_config_service_name(self):
        conf = Config(config={
            'service_name': 'my-app'
        })
        assert conf.service_name == 'my-app'

        conf = Config(config={}, service_name='my-app')
        assert conf.service_name == 'my-app'

    def test_config_service_name_exc(self):
        with pytest.raises(ConfigException):
            conf = Config(config={}) # noqa

    def test_config_init_tracer(self):
        conf = Config(config={}, service_name='my-app')
        tracer = conf.set_tracer()

        assert opentracing.tracer == tracer
