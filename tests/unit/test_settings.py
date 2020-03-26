import pytest

from ddtrace.settings import Config, HttpConfig, IntegrationConfig

from ..base import BaseTestCase


class TestConfig(BaseTestCase):
    def test_environment_analytics_enabled(self):
        with self.override_env(dict(DD_ANALYTICS_ENABLED='True')):
            config = Config()
            self.assertTrue(config.analytics_enabled)

        with self.override_env(dict(DD_ANALYTICS_ENABLED='False')):
            config = Config()
            self.assertFalse(config.analytics_enabled)

        with self.override_env(dict(DD_TRACE_ANALYTICS_ENABLED='True')):
            config = Config()
            self.assertTrue(config.analytics_enabled)

        with self.override_env(dict(DD_TRACE_ANALYTICS_ENABLED='False')):
            config = Config()
            self.assertFalse(config.analytics_enabled)

    def test_environment_analytics_overrides(self):
        with self.override_env(dict(DD_ANALYTICS_ENABLED='False', DD_TRACE_ANALYTICS_ENABLED='True')):
            config = Config()
            self.assertTrue(config.analytics_enabled)

        with self.override_env(dict(DD_ANALYTICS_ENABLED='False', DD_TRACE_ANALYTICS_ENABLED='False')):
            config = Config()
            self.assertFalse(config.analytics_enabled)

        with self.override_env(dict(DD_ANALYTICS_ENABLED='True', DD_TRACE_ANALYTICS_ENABLED='True')):
            config = Config()
            self.assertTrue(config.analytics_enabled)

        with self.override_env(dict(DD_ANALYTICS_ENABLED='True', DD_TRACE_ANALYTICS_ENABLED='False')):
            config = Config()
            self.assertFalse(config.analytics_enabled)

    def test_logs_injection(self):
        with self.override_env(dict(DD_LOGS_INJECTION='True')):
            config = Config()
            self.assertTrue(config.logs_injection)

        with self.override_env(dict(DD_LOGS_INJECTION='false')):
            config = Config()
            self.assertFalse(config.logs_injection)

    def test_service(self):
        # If none is provided the default should be ``None``
        with self.override_env(dict()):
            config = Config()
            self.assertEqual(config.service, None)

        with self.override_env(dict(DD_SERVICE="my-service")):
            config = Config()
            self.assertEqual(config.service, "my-service")


class TestHttpConfig(BaseTestCase):

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


class TestIntegrationConfigCore(BaseTestCase):
    def test_init_simple(self):
        c = IntegrationConfig(None, "int", dict(key1="value1", key2="value2", key3=3,))
        assert "key1" in c
        assert "key2" in c
        assert "key3" in c

    def test_init_complex(self):
        with self.override_env(dict(DD_INT_KEY1="DNE", DD_INT_KEY2="val2", DD_INT_KEY3="1.4")):
            c = IntegrationConfig(None, "int", dict(
                key1=IntegrationConfig.Dec("value1"),
                key2=IntegrationConfig.Dec("value2", env_var=True),
                key3=IntegrationConfig.Dec(2.3, env_var=True, type=float),
            ))
        assert "key1" in c
        assert "key2" in c
        assert "key3" in c

        assert c.key1 == "value1"
        assert c._is_lib_defined("key1")
        assert c.key2 == "val2"
        assert c._is_user_defined("key2")
        assert c.key3 == 1.4
        assert c._is_user_defined("key3")

    def test_dot_operator_not_added(self):
        """
        When a config setting has not been added
            It is an error to access it via dot notation.
            It is an error to set it via dot notation.
        """
        c = IntegrationConfig(None, "int")

        with pytest.raises(IntegrationConfig.IntegrationConfigAttributeError):
            _ = c.key

        with pytest.raises(IntegrationConfig.IntegrationConfigAttributeError):
            c.key = object()

        with pytest.raises(IntegrationConfig.IntegrationConfigAttributeError):
            _ = c.key

        assert "key" not in c

    def test_index_operator_not_added(self):
        """
        When a config setting has not been added
            It is an error to access it via index notation.
            It is an error to set it via index notation.
        """
        c = IntegrationConfig(None, "int")

        with pytest.raises(IntegrationConfig.IntegrationConfigKeyError):
            _ = c["key"]

        with pytest.raises(IntegrationConfig.IntegrationConfigKeyError):
            c["key"] = object()

        with pytest.raises(IntegrationConfig.IntegrationConfigKeyError):
            _ = c["key"]

    def test_index_operator(self):
        c = IntegrationConfig(None, "int", dict(key="test",))
        value = object()
        c["key"] = value
        assert "key" in c
        assert c["key"] == value

    def test_dot_operator_set(self):
        c = IntegrationConfig(None, "int", dict(key="value"))
        value = object()
        c.key = value
        assert "key" in c
        assert c["key"] == value
        assert c.key == value

    def test_initializer_values(self):
        """
        When values are added via the initializer
            They should be library defined.
        """
        c = IntegrationConfig(None, "int", dict(key1="value", key2="value",))

        assert c._is_lib_defined("key1")
        assert c._is_lib_defined("key2")

    def test_add_values(self):
        """
        When values are added via the initializer
            They should be library defined.
        """
        c = IntegrationConfig(None, "int", dict(key1="value",))

        c._add("key2", "value")

        assert c._is_lib_defined("key1")
        assert c._is_lib_defined("key2")

    def test_reset(self):
        c = IntegrationConfig(None, "int", dict(key1="value",))
        c.key1 = "different"
        c._reset()
        assert c.key1 == "value"

    def test_items(self):
        items = dict(key1="value", key2="value2")
        c = IntegrationConfig(None, "int", items)
        citems = c.items()
        assert ("key1", "value") in citems
        assert ("key2", "value2") in citems

    def test_get(self):
        c = IntegrationConfig(None, "int", dict(key1="value",))
        assert c.get("key1") == "value"
        assert c.get("dne", default=5) == 5

    def test_mixed_access_attr(self):
        c = IntegrationConfig(None, "int", dict(key1="value",))
        c.key1 = "value2"
        assert c["key1"] == "value2"
        assert c.get("key1") == "value2"

        c["key1"] = "value3"
        assert c.key1 == "value3"
        assert c.get("key1") == "value3"


class TestIntegrationConfig(BaseTestCase):
    def setUp(self):
        self.config = Config()
        self.integration_config = IntegrationConfig(self.config, "test")

    def test_allow_both_access(self):
        self.integration_config._add("setting", None)

        self.integration_config.setting = "value"
        assert self.integration_config["setting"] == "value"
        assert self.integration_config.setting == "value"

        self.integration_config["setting"] = "new-value"
        assert self.integration_config.setting == "new-value"
        assert self.integration_config["setting"] == "new-value"

    def test_allow_configuring_http(self):
        self.integration_config.http.trace_headers("integration_header")
        assert self.integration_config.http.header_is_traced("integration_header")
        assert not self.integration_config.http.header_is_traced("other_header")

    def test_allow_exist_both_global_and_integration_config(self):
        self.config.trace_headers("global_header")
        assert self.integration_config.header_is_traced("global_header")

        self.integration_config.http.trace_headers("integration_header")
        assert self.integration_config.header_is_traced("integration_header")
        assert not self.integration_config.header_is_traced("global_header")
        assert not self.config.header_is_traced("integration_header")

    def test_environment_analytics_enabled(self):
        self.assertFalse(self.config.analytics_enabled)

        assert self.config.foo.analytics_enabled is False

        with self.override_env(dict(DD_ANALYTICS_ENABLED="True")):
            config = Config()
            self.assertTrue(config.analytics_enabled)
            assert self.config.foo.analytics_enabled is False
            assert not self.config.foo._is_user_defined("analytics_enabled")

        with self.override_env(dict(DD_FOO_ANALYTICS_ENABLED="True")):
            config = Config()
            self.assertTrue(config.foo.analytics_enabled)
            self.assertEqual(config.foo.analytics_sample_rate, 1.0)
            assert config.foo._is_user_defined("analytics_enabled")

        with self.override_env(dict(DD_FOO_ANALYTICS_ENABLED="False")):
            config = Config()
            self.assertFalse(config.foo.analytics_enabled)

        with self.override_env(dict(DD_FOO_ANALYTICS_ENABLED="True", DD_FOO_ANALYTICS_SAMPLE_RATE="0.5")):
            config = Config()
            self.assertTrue(config.foo.analytics_enabled)
            self.assertEqual(config.foo.analytics_sample_rate, 0.5)

    def test_analytics_enabled_attribute(self):
        """" Confirm environment variable and kwargs are handled properly """
        ic = IntegrationConfig(self.config, "foo", analytics_enabled=True)
        self.assertTrue(ic.analytics_enabled)

        ic = IntegrationConfig(self.config, "foo", analytics_enabled=False)
        self.assertFalse(ic.analytics_enabled)

        with self.override_env(dict(DD_FOO_ANALYTICS_ENABLED="True")):
            ic = IntegrationConfig(self.config, "foo")
            ic.analytics_enabled = False
            self.assertFalse(ic.analytics_enabled)

    def test_get_analytics_sample_rate(self):
        """" Check method for accessing sample rate based on configuration """
        ic = IntegrationConfig(self.config, "foo", analytics_enabled=True, analytics_sample_rate=0.5)
        self.assertEqual(ic.get_analytics_sample_rate(), 0.5)

        ic = IntegrationConfig(self.config, "foo", analytics_enabled=True)
        self.assertEqual(ic.get_analytics_sample_rate(), 1.0)

        ic = IntegrationConfig(self.config, "foo", analytics_enabled=True, analytics_sample_rate=None)
        # uhhhhhhhh?
        assert ic.get_analytics_sample_rate() is True

        ic = IntegrationConfig(self.config, "foo", analytics_enabled=False)
        self.assertIsNone(ic.get_analytics_sample_rate())

        with self.override_env(dict(DD_ANALYTICS_ENABLED="True")):
            config = Config()
            ic = IntegrationConfig(config, "foo")
            self.assertEqual(ic.get_analytics_sample_rate(use_global_config=True), 1.0)

        with self.override_env(dict(DD_ANALYTICS_ENABLED="False")):
            config = Config()
            ic = IntegrationConfig(config, "foo")
            self.assertIsNone(ic.get_analytics_sample_rate(use_global_config=True))
