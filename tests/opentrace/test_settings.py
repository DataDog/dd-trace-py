import unittest
import pytest

from ddtrace.settings import ConfigException
from ddtrace.opentrace import Config


class OpenTraceConfigTest(unittest.TestCase):
    def setUp(self):
        pass

    def tearDown(self):
        pass

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
