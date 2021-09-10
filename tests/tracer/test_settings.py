import pytest

from ddtrace.settings import Config
from ddtrace.settings import HttpConfig
from ddtrace.settings import IntegrationConfig
from tests.utils import BaseTestCase
from tests.utils import override_env


class TestConfig(BaseTestCase):
    def test_environment_analytics_enabled(self):
        with self.override_env(dict(DD_ANALYTICS_ENABLED="True")):
            config = Config()
            self.assertTrue(config.analytics_enabled)

        with self.override_env(dict(DD_ANALYTICS_ENABLED="False")):
            config = Config()
            self.assertFalse(config.analytics_enabled)

        with self.override_env(dict(DD_TRACE_ANALYTICS_ENABLED="True")):
            config = Config()
            self.assertTrue(config.analytics_enabled)

        with self.override_env(dict(DD_TRACE_ANALYTICS_ENABLED="False")):
            config = Config()
            self.assertFalse(config.analytics_enabled)

    def test_environment_analytics_overrides(self):
        with self.override_env(dict(DD_ANALYTICS_ENABLED="False", DD_TRACE_ANALYTICS_ENABLED="True")):
            config = Config()
            self.assertTrue(config.analytics_enabled)

        with self.override_env(dict(DD_ANALYTICS_ENABLED="False", DD_TRACE_ANALYTICS_ENABLED="False")):
            config = Config()
            self.assertFalse(config.analytics_enabled)

        with self.override_env(dict(DD_ANALYTICS_ENABLED="True", DD_TRACE_ANALYTICS_ENABLED="True")):
            config = Config()
            self.assertTrue(config.analytics_enabled)

        with self.override_env(dict(DD_ANALYTICS_ENABLED="True", DD_TRACE_ANALYTICS_ENABLED="False")):
            config = Config()
            self.assertFalse(config.analytics_enabled)

    def test_logs_injection(self):
        with self.override_env(dict(DD_LOGS_INJECTION="True")):
            config = Config()
            self.assertTrue(config.logs_injection)

        with self.override_env(dict(DD_LOGS_INJECTION="false")):
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

    def test_http_config(self):
        config = Config()
        config._add("django", dict())
        assert config.django.trace_query_string is None
        config.http.trace_query_string = True
        assert config.http.trace_query_string is True
        assert config.django.trace_query_string is True

        # Integration usage
        config = Config()
        config._add("django", dict())
        config.django.http.trace_query_string = True
        assert config.http.trace_query_string is None
        assert config.django.trace_query_string is True
        assert config.django.http.trace_query_string is True


class TestHttpConfig(BaseTestCase):
    def test_trace_headers(self):
        http_config = HttpConfig()
        http_config.trace_headers("some_header")
        assert http_config.header_is_traced("some_header")
        assert not http_config.header_is_traced("some_other_header")

    def test_trace_headers_whitelist_case_insensitive(self):
        http_config = HttpConfig()
        http_config.trace_headers("some_header")
        assert http_config.header_is_traced("sOmE_hEaDeR")
        assert not http_config.header_is_traced("some_other_header")

    def test_trace_multiple_headers(self):
        http_config = HttpConfig()
        http_config.trace_headers(["some_header_1", "some_header_2"])
        assert http_config.header_is_traced("some_header_1")
        assert http_config.header_is_traced("some_header_2")
        assert not http_config.header_is_traced("some_header_3")

    def test_empty_entry_do_not_raise_exception(self):
        http_config = HttpConfig()
        http_config.trace_headers("")

        assert not http_config.header_is_traced("some_header_1")

    def test_none_entry_do_not_raise_exception(self):
        http_config = HttpConfig()
        http_config.trace_headers(None)
        assert not http_config.header_is_traced("some_header_1")

    def test_is_header_tracing_configured(self):
        http_config = HttpConfig()
        assert not http_config.is_header_tracing_configured
        http_config.trace_headers("some_header")
        assert http_config.is_header_tracing_configured

    def test_header_is_traced_case_insensitive(self):
        http_config = HttpConfig()
        http_config.trace_headers("sOmE_hEaDeR")
        assert http_config.header_is_traced("SoMe_HeAdEr")
        assert not http_config.header_is_traced("some_other_header")

    def test_header_is_traced_false_for_empty_header(self):
        http_config = HttpConfig()
        http_config.trace_headers("some_header")
        assert not http_config.header_is_traced("")

    def test_header_is_traced_false_for_none_header(self):
        http_config = HttpConfig()
        http_config.trace_headers("some_header")
        assert not http_config.header_is_traced(None)


class TestIntegrationConfig(BaseTestCase):
    def setUp(self):
        self.config = Config()
        self.integration_config = IntegrationConfig(self.config, "test")

    def test_is_a_dict(self):
        assert isinstance(self.integration_config, dict)

    def test_allow_item_access(self):
        self.integration_config["setting"] = "value"

        # Can be accessed both as item and attr accessor
        assert self.integration_config.setting == "value"
        assert self.integration_config["setting"] == "value"

    def test_allow_attr_access(self):
        self.integration_config.setting = "value"

        # Can be accessed both as item and attr accessor
        assert self.integration_config.setting == "value"
        assert self.integration_config["setting"] == "value"

    def test_allow_both_access(self):
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

        assert not self.integration_config.http.header_is_traced("global_header")
        assert not self.config.header_is_traced("integration_header")

    def test_environment_analytics_enabled(self):
        # default
        self.assertFalse(self.config.analytics_enabled)
        self.assertIsNone(self.config.foo.analytics_enabled)

        with self.override_env(dict(DD_ANALYTICS_ENABLED="True")):
            config = Config()
            self.assertTrue(config.analytics_enabled)
            self.assertIsNone(config.foo.analytics_enabled)

        with self.override_env(dict(DD_TRACE_ANALYTICS_ENABLED="True")):
            config = Config()
            self.assertTrue(config.analytics_enabled)
            self.assertIsNone(config.foo.analytics_enabled)

        with self.override_env(dict(DD_FOO_ANALYTICS_ENABLED="True")):
            config = Config()
            self.assertTrue(config.foo.analytics_enabled)
            self.assertEqual(config.foo.analytics_sample_rate, 1.0)

        with self.override_env(dict(DD_TRACE_FOO_ANALYTICS_ENABLED="True")):
            config = Config()
            self.assertTrue(config.foo.analytics_enabled)
            self.assertEqual(config.foo.analytics_sample_rate, 1.0)

        with self.override_env(dict(DD_FOO_ANALYTICS_ENABLED="False")):
            config = Config()
            self.assertFalse(config.foo.analytics_enabled)

        with self.override_env(dict(DD_TRACE_FOO_ANALYTICS_ENABLED="False")):
            config = Config()
            self.assertFalse(config.foo.analytics_enabled)

        with self.override_env(dict(DD_FOO_ANALYTICS_ENABLED="True", DD_FOO_ANALYTICS_SAMPLE_RATE="0.5")):
            config = Config()
            self.assertTrue(config.foo.analytics_enabled)
            self.assertEqual(config.foo.analytics_sample_rate, 0.5)

        with self.override_env(dict(DD_TRACE_FOO_ANALYTICS_ENABLED="True", DD_TRACE_FOO_ANALYTICS_SAMPLE_RATE="0.5")):
            config = Config()
            self.assertTrue(config.foo.analytics_enabled)
            self.assertEqual(config.foo.analytics_sample_rate, 0.5)

    def test_analytics_enabled_attribute(self):
        """Confirm environment variable and kwargs are handled properly"""
        ic = IntegrationConfig(self.config, "foo", analytics_enabled=True)
        self.assertTrue(ic.analytics_enabled)

        ic = IntegrationConfig(self.config, "foo", analytics_enabled=False)
        self.assertFalse(ic.analytics_enabled)

        with self.override_env(dict(DD_FOO_ANALYTICS_ENABLED="True")):
            ic = IntegrationConfig(self.config, "foo", analytics_enabled=False)
            self.assertFalse(ic.analytics_enabled)

        with self.override_env(dict(DD_TRACE_FOO_ANALYTICS_ENABLED="True")):
            ic = IntegrationConfig(self.config, "foo", analytics_enabled=False)
            self.assertFalse(ic.analytics_enabled)

    def test_get_analytics_sample_rate(self):
        """Check method for accessing sample rate based on configuration"""
        ic = IntegrationConfig(self.config, "foo", analytics_enabled=True, analytics_sample_rate=0.5)
        self.assertEqual(ic.get_analytics_sample_rate(), 0.5)

        ic = IntegrationConfig(self.config, "foo", analytics_enabled=True)
        self.assertEqual(ic.get_analytics_sample_rate(), 1.0)

        ic = IntegrationConfig(self.config, "foo", analytics_enabled=False)
        self.assertIsNone(ic.get_analytics_sample_rate())

        with self.override_env(dict(DD_ANALYTICS_ENABLED="True")):
            config = Config()
            ic = IntegrationConfig(config, "foo")
            self.assertEqual(ic.get_analytics_sample_rate(use_global_config=True), 1.0)

        with self.override_env(dict(DD_TRACE_ANALYTICS_ENABLED="True")):
            config = Config()
            ic = IntegrationConfig(config, "foo")
            self.assertEqual(ic.get_analytics_sample_rate(use_global_config=True), 1.0)

        with self.override_env(dict(DD_ANALYTICS_ENABLED="False")):
            config = Config()
            ic = IntegrationConfig(config, "foo")
            self.assertIsNone(ic.get_analytics_sample_rate(use_global_config=True))

        with self.override_env(dict(DD_TRACE_ANALYTICS_ENABLED="False")):
            config = Config()
            ic = IntegrationConfig(config, "foo")
            self.assertIsNone(ic.get_analytics_sample_rate(use_global_config=True))

    def test_service(self):
        ic = IntegrationConfig(self.config, "foo")
        assert ic.service is None

    @BaseTestCase.run_in_subprocess(env_overrides=dict(DD_FOO_SERVICE="foo-svc"))
    def test_service_env_var(self):
        ic = IntegrationConfig(self.config, "foo")
        assert ic.service == "foo-svc"

    @BaseTestCase.run_in_subprocess(env_overrides=dict(DATADOG_FOO_SERVICE="foo-svc"))
    def test_service_env_var_legacy(self):
        ic = IntegrationConfig(self.config, "foo")
        assert ic.service == "foo-svc"

    @BaseTestCase.run_in_subprocess(env_overrides=dict(DD_FOO_SERVICE_NAME="foo-svc"))
    def test_service_name_env_var(self):
        ic = IntegrationConfig(self.config, "foo")
        assert ic.service == "foo-svc"

    @BaseTestCase.run_in_subprocess(env_overrides=dict(DATADOG_FOO_SERVICE_NAME="foo-svc"))
    def test_service_name_env_var_legacy(self):
        ic = IntegrationConfig(self.config, "foo")
        assert ic.service == "foo-svc"


@pytest.mark.parametrize(
    "global_headers,int_headers,expected",
    (
        (None, None, (False, False, False)),
        ([], None, (False, False, False)),
        (["Header"], None, (True, False, True)),
        (None, ["Header"], (False, True, True)),
        (None, [], (False, False, False)),
        (["Header"], ["Header"], (True, True, True)),
        ([], [], (False, False, False)),
    ),
)
def test_config_is_header_tracing_configured(global_headers, int_headers, expected):
    config = Config()
    integration_config = config.myint

    if global_headers is not None:
        config.trace_headers(global_headers)
    if int_headers is not None:
        integration_config.http.trace_headers(int_headers)

    assert (
        config.http.is_header_tracing_configured,
        integration_config.http.is_header_tracing_configured,
        integration_config.is_header_tracing_configured,
    ) == expected


def test_environment_header_tags():
    with override_env(dict(DD_TRACE_HEADER_TAGS="Host:http.host,User-agent:http.user_agent")):
        config = Config()

    assert config.http.is_header_tracing_configured
    assert config._header_tag_name("Host") == "http.host"
    assert config._header_tag_name("User-agent") == "http.user_agent"
    # Case insensitive
    assert config._header_tag_name("User-Agent") == "http.user_agent"
