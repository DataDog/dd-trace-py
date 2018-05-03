from unittest import TestCase

from nose.tools import eq_, ok_, assert_raises

from ddtrace import config as global_config
from ddtrace.settings import Config, ConfigException


class GlobalConfigTestCase(TestCase):
    """Test the `Configuration` class that stores integration settings"""
    def setUp(self):
        self.config = Config()

    def test_registration(self):
        # ensure an integration can register a new list of settings
        settings = {
            'distributed_tracing': True,
        }
        self.config._add('requests', settings)
        ok_(self.config.requests['distributed_tracing'] is True)

    def test_settings_copy(self):
        # ensure that once an integration is registered, a copy
        # of the settings is stored to avoid side-effects
        experimental = {
            'request_enqueuing': True,
        }
        settings = {
            'distributed_tracing': True,
            'experimental': experimental,
        }
        self.config._add('requests', settings)

        settings['distributed_tracing'] = False
        experimental['request_enqueuing'] = False
        ok_(self.config.requests['distributed_tracing'] is True)
        ok_(self.config.requests['experimental']['request_enqueuing'] is True)

    def test_missing_integration(self):
        # ensure a meaningful exception is raised when an integration
        # that is not available is retrieved in the configuration
        # object
        with assert_raises(ConfigException) as e:
            self.config.new_integration['some_key']

        ok_(isinstance(e.exception, ConfigException))

    def test_global_configuration(self):
        # ensure a global configuration is available in the `ddtrace` module
        ok_(isinstance(global_config, Config))
