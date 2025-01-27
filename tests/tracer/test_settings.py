import warnings

import pytest

from ddtrace.settings import Config
from ddtrace.settings import HttpConfig
from ddtrace.settings import IntegrationConfig
from tests.utils import BaseTestCase
from tests.utils import override_env


class TestConfig(BaseTestCase):
    def test_logs_injection(self):
        with self.override_env(dict(DD_LOGS_INJECTION="True")):
            config = Config()
            self.assertTrue(config._logs_injection)

        with self.override_env(dict(DD_LOGS_INJECTION="false")):
            config = Config()
            self.assertFalse(config._logs_injection)

    def test_service(self):
        # If none is provided the default should be ``None``
        with self.override_env(dict()):
            config = Config()
            self.assertEqual(config.service, "tests.tracer")

        with self.override_env(dict(DD_SERVICE="my-service")):
            config = Config()
            self.assertEqual(config.service, "my-service")

    def test_http_config(self):
        config = Config()
        config._add("django", dict())
        assert config.django.trace_query_string is None
        config._http.trace_query_string = True
        assert config._http.trace_query_string is True
        assert config.django.trace_query_string is True

        # Integration usage
        config = Config()
        config._add("django", dict())
        config.django.http.trace_query_string = True
        assert config._http.trace_query_string is None
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

    def test_service(self):
        ic = IntegrationConfig(self.config, "foo")
        assert ic.service is None

    @BaseTestCase.run_in_subprocess(env_overrides=dict(DD_FOO_SERVICE="foo-svc"))
    def test_service_env_var(self):
        ic = IntegrationConfig(self.config, "foo")
        assert ic.service == "foo-svc"

    @BaseTestCase.run_in_subprocess(env_overrides=dict(DD_FOO_SERVICE_NAME="foo-svc"))
    def test_service_name_env_var(self):
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
        config._http.is_header_tracing_configured,
        integration_config.http.is_header_tracing_configured,
        integration_config.is_header_tracing_configured,
    ) == expected


def test_environment_header_tags():
    with override_env(dict(DD_TRACE_HEADER_TAGS="Host:http.host,User-agent:http.user_agent")):
        config = Config()

    assert config._http.is_header_tracing_configured
    assert config._header_tag_name("Host") == "http.host"
    assert config._header_tag_name("User-agent") == "http.user_agent"
    # Case insensitive
    assert config._header_tag_name("User-Agent") == "http.user_agent"


@pytest.mark.parametrize(
    "env,expected",
    (
        (dict(), (512, True)),
        (dict(DD_TRACE_X_DATADOG_TAGS_MAX_LENGTH="0"), (0, False)),
        (dict(DD_TRACE_X_DATADOG_TAGS_MAX_LENGTH="1024"), (1024, True)),
        (dict(DD_TRACE_X_DATADOG_TAGS_MAX_LENGTH="-1"), (0, False)),
    ),
)
def test_x_datadog_tags(env, expected):
    with override_env(env):
        _ = Config()
        assert expected == (_._x_datadog_tags_max_length, _._x_datadog_tags_enabled)


@pytest.mark.parametrize(
    "deprecated_name,name,test_value,env",
    (
        ("http_tag_query_string", "_http_tag_query_string", True, "DD_TRACE_HTTP_CLIENT_TAG_QUERY_STRING"),
        ("trace_http_header_tags", "_trace_http_header_tags", {"x-dd": "x_dd"}, "DD_TRACE_HEADER_TAGS"),
        ("report_hostname", "_report_hostname", True, "DD_TRACE_REPORT_HOSTNAME"),
        ("health_metrics_enabled", "_health_metrics_enabled", True, "DD_TRACE_HEALTH_METRICS_ENABLED"),
        ("analytics_enabled", "_analytics_enabled", True, "DD_TRACE_ANALYTICS_ENABLED"),
        ("client_ip_header", "_client_ip_header", True, "DD_TRACE_CLIENT_IP_HEADER"),
        ("retrieve_client_ip", "_retrieve_client_ip", True, "DD_TRACE_CLIENT_IP_ENABLED"),
        (
            "propagation_http_baggage_enabled",
            "_propagation_http_baggage_enabled",
            True,
            "DD_TRACE_PROPAGATION_HTTP_BAGGAGE_ENABLED",
        ),
        (
            "global_query_string_obfuscation_disabled",
            "_global_query_string_obfuscation_disabled",
            True,
            'DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP=""',
        ),
        ("trace_methods", "_trace_methods", ["monkey.banana_melon"], "DD_TRACE_METHODS"),
        ("ci_visibility_log_level", "_ci_visibility_log_level", True, "DD_CIVISIBILITY_LOG_LEVEL"),
        ("test_session_name", "_test_session_name", "yessirapp", "DD_TEST_SESSION_NAME"),
        ("logs_injection", "_logs_injection", False, "DD_LOGS_INJECTION"),
    ),
)
def test_deprecated_config_attributes(deprecated_name, name, test_value, env):
    """Ensures setting and getting deprecated attributes log a warning and still
    set/return the expected values.
    """
    with warnings.catch_warnings(record=True) as warns:
        warnings.simplefilter("always")
        config = Config()
        # Test getting/setting a configuration by the expected name
        setattr(config, name, test_value)
        assert getattr(config, name) == test_value
        assert len(warns) == 0
        expected_warning = (
            f"ddtrace.config.{deprecated_name} is deprecated and will be "
            f"removed in version '3.0.0': Use the environment variable {env} "
            "instead. This variable must be set before importing ddtrace."
        )
        # Test getting the configuration by the deprecated name
        getattr(config, deprecated_name) == test_value
        assert len(warns) == 1
        assert str(warns[0].message) == expected_warning
        # Test setting the configuration by the deprecated name
        setattr(config, deprecated_name, None)
        assert getattr(config, name) is None
        assert len(warns) == 2
        assert str(warns[1].message) == expected_warning
