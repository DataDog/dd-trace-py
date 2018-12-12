import mock
import os
import unittest

from ddtrace.settings import Config, IntegrationConfig, HttpConfig


class TestHttpConfig(object):

    def test_trace_headers(self):
        http_config = HttpConfig()
        http_config.trace_headers('some_header')
        assert http_config.header_is_traced('some_header')
        assert not http_config.header_is_traced('some_other_header')

    def test_trace_headers_whitelist_case_insensitive(self):
        http_config = HttpConfig()
        http_config.trace_headers('some_header')
        assert http_config.header_is_traced('sOmE_hEaDeR')
        assert not http_config.header_is_traced('some_other_header')

    def test_trace_multiple_headers(self):
        http_config = HttpConfig()
        http_config.trace_headers(['some_header_1', 'some_header_2'])
        assert http_config.header_is_traced('some_header_1')
        assert http_config.header_is_traced('some_header_2')
        assert not http_config.header_is_traced('some_header_3')

    def test_empty_entry_do_not_raise_exception(self):
        http_config = HttpConfig()
        http_config.trace_headers('')
        assert not http_config.header_is_traced('some_header_1')

    def test_none_entry_do_not_raise_exception(self):
        http_config = HttpConfig()
        http_config.trace_headers(None)
        assert not http_config.header_is_traced('some_header_1')

    def test_is_header_tracing_configured(self):
        http_config = HttpConfig()
        assert not http_config.is_header_tracing_configured
        http_config.trace_headers('some_header')
        assert http_config.is_header_tracing_configured

    def test_header_is_traced_case_insensitive(self):
        http_config = HttpConfig()
        http_config.trace_headers('sOmE_hEaDeR')
        assert http_config.header_is_traced('SoMe_HeAdEr')
        assert not http_config.header_is_traced('some_other_header')

    def test_header_is_traced_false_for_empty_header(self):
        http_config = HttpConfig()
        http_config.trace_headers('some_header')
        assert not http_config.header_is_traced('')

    def test_header_is_traced_false_for_none_header(self):
        http_config = HttpConfig()
        http_config.trace_headers('some_header')
        assert not http_config.header_is_traced(None)


class TestIntegrationConfig(unittest.TestCase):

    def test_is_a_dict(self):
        integration_config = IntegrationConfig(Config(), '')
        assert isinstance(integration_config, dict)

    def test_allow_item_access(self):
        config = IntegrationConfig(Config(), '')
        config['setting'] = 'value'

        # Can be accessed both as item and attr accessor
        assert config.setting == 'value'
        assert config['setting'] == 'value'

    def test_allow_attr_access(self):
        config = IntegrationConfig(Config(), '')
        config.setting = 'value'

        # Can be accessed both as item and attr accessor
        assert config.setting == 'value'
        assert config['setting'] == 'value'

    def test_allow_both_access(self):
        config = IntegrationConfig(Config(), '')

        config.setting = 'value'
        assert config['setting'] == 'value'
        assert config.setting == 'value'

        config['setting'] = 'new-value'
        assert config.setting == 'new-value'
        assert config['setting'] == 'new-value'

    def test_allow_configuring_http(self):
        global_config = Config()
        integration_config = IntegrationConfig(global_config, '')
        integration_config.http.trace_headers('integration_header')
        assert integration_config.http.header_is_traced('integration_header')
        assert not integration_config.http.header_is_traced('other_header')

    def test_allow_exist_both_global_and_integration_config(self):
        global_config = Config()
        integration_config = IntegrationConfig(global_config, '')

        global_config.trace_headers('global_header')
        assert integration_config.header_is_traced('global_header')

        integration_config.http.trace_headers('integration_header')
        assert integration_config.header_is_traced('integration_header')
        assert not integration_config.header_is_traced('global_header')
        assert not global_config.header_is_traced('integration_header')

    def test_multiple_set(self):
        config = IntegrationConfig(Config(), '')
        config.service_name = 'test'
        self.assertEqual(config.service_name, 'test')
        config.service_name = 'test2'
        self.assertEqual(config.service_name, 'test2')

    def test_no_attr(self):
        """
        An AttributeError should be raised if an item is not stored in the config.
        """
        config = IntegrationConfig(Config(), '')
        with self.assertRaises(AttributeError):
            test = config.dne  # noqa

    def test_add_default(self):
        """
        Test that _add_default adds a default value to the config.
        """
        redis_config = Config().redis
        redis_config._add_default('key1', 'value1')
        redis_config._add_default('key2', 'value2')
        self.assertEqual(redis_config.key1, 'value1')
        self.assertEqual(redis_config.key2, 'value2')

    def test_default_override(self):
        """
        Test that a default can be overridden with a user specified value.
        """
        redis_config = Config().redis
        redis_config._add_default('key1', 'value1')
        redis_config.key1 = 'uservalue1'
        self.assertEqual(redis_config.key1, 'uservalue1')

    @mock.patch.dict(os.environ, {'DD_REDIS_SERVICE_NAME': 'env-redis'})
    def test_default_environment_variable(self):
        """
        Test that an environment variable overrides the default value.
        """
        redis_config = Config().redis
        redis_config._add_default('service_name', 'my-redis-service')
        self.assertEqual(redis_config.service_name, 'env-redis')

    @mock.patch.dict(os.environ, {'DATADOG_REDIS_SERVICE_NAME': 'env-redis'})
    def test_default_environment_variable_legacy(self):
        """
        Same as test_default_environment_variable but with the legacy naming.
        """
        with mock.patch('warnings.warn') as mock_warn:
            redis_config = Config().redis
            redis_config._add_default('service_name', 'my-redis-service')
            self.assertEqual(redis_config.service_name, 'env-redis')
            self.assertTrue(mock_warn.call_count > 0)

    @mock.patch.dict(os.environ, {'DD_REDIS_SERVICE_NAME': 'redis_service_name'})
    def test_no_default_environment_variable(self):
        """
        When no default is provided but an environment variable is set
            An AttributeError should be raised
        """
        integration_config = Config().redis
        with self.assertRaises(AttributeError):
            self.assertEqual(integration_config.service_name, 'redis_service_name')

    def test_precedence(self):
        """
        Test using the config with defaults, environment variables and user
        overrides.
        """
        redis_config = Config().redis

        # Sanity check the default.
        redis_config._add_default('service_name', 'dd_service_name')
        self.assertEqual(redis_config.service_name, 'dd_service_name')

        # Check the environment variable.
        with mock.patch.dict(os.environ, {'DATADOG_REDIS_SERVICE_NAME': 'ude_service_name'}):
            self.assertEqual(redis_config.service_name, 'ude_service_name')

            # Manual user override while still having the environment variable.
            redis_config.service_name = 'ud_service_name'
            self.assertEqual(redis_config.service_name, 'ud_service_name')

            # Check a second manual override.
            redis_config.service_name = 'ud2_service_name'
            self.assertEqual(redis_config.service_name, 'ud2_service_name')

    def test_del_key(self):
        """
        Test that a user override can be deleted.

        TODO: is this the behaviour we want?
        """
        redis_config = Config().redis
        redis_config._add_default('service_name', 'dd_service_name')
        redis_config.service_name = 'custom_service_name'
        self.assertEqual(redis_config.service_name, 'custom_service_name')

        del redis_config['service_name']
        self.assertEqual(redis_config.service_name, 'custom_service_name')
