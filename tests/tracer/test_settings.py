import pytest

from ddtrace.settings import HttpConfig
from ddtrace.settings import IntegrationConfig
from ddtrace.settings._config import Config
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

    def test_app_analytics_property(self):
        self.integration_config.name = "test-integration"
       
        # test default values
        assert self.integration_config.analytics_enabled == False
        assert self.integration_config.analytics_sample_rate == 1.0
        
        # test updating values
        self.integration_config.analytics_enabled = True
        assert self.integration_config.analytics_enabled == True

        self.integration_config.analytics_sample_rate = 0.5
        assert self.integration_config.analytics_sample_rate == 0.5

        assert self.integration_config.get_analytics_sample_rate() == 1

    def test_app_analytics_deprecation(self):
        import warnings
        with warnings.catch_warnings(record=True) as warns:
            warnings.simplefilter("always")
            
            self.integration_config.name = "test-integration"


            #self.integration_config.analytics_enabled = True
            self.integration_config.analytics_enabled
            getter_warning = str(warns[0].message)
            assert "analytics_enabled is deprecated" in str(getter_warning)
            assert "See the documentation migrate to the new configuration options: https://docs.datadoghq.com/tracing/legacy_app_analytics/?code-lang=python#migrate-to-the-new-configuration-options" in str(getter_warning)
            assert "will be removed in version '4.0.0'" in str(getter_warning)
            
            self.integration_config.analytics_sample_rate
            setter_warning = str(warns[1].message)
            assert "analytics_sample_rate is deprecated" in str(setter_warning)
            assert "See the documentation migrate to the new configuration options: https://docs.datadoghq.com/tracing/legacy_app_analytics/?code-lang=python#migrate-to-the-new-configuration-options" in str(setter_warning)
            assert "will be removed in version '4.0.0'" in str(setter_warning)

            self.integration_config.get_analytics_sample_rate()
            getter_warning = str(warns[2].message)
            assert "get_analytics_sample_rate is deprecated" in str(getter_warning)
            assert "The method currently returns 1 always" in str(getter_warning)
            assert "will be removed in version '4.0.0'" in str(getter_warning)
            
            assert len(warns) == 3



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


# @pytest.mark.subprocess()
# def test_get_analytics_sample_rate_deprecation(env_overrides=dict(DD_TEST_ANALYTICS_ENABLED="true")):
#     """Ensure App Analytics deprecation warning shows"""
#     import warnings
#     with warnings.catch_warnings(record=True) as warns:
#         warnings.simplefilter("always")
        
#         from ddtrace.settings._config import Config
#         from ddtrace.settings import IntegrationConfig

#         config = Config()
#         ic = IntegrationConfig(config, "test")
        
#         # test default values
#         assert ic.analytics_enabled == False
#         assert ic.analytics_sample_rate == 1.0

#         # test updating values
#         ic.analytics_enabled = True
#         assert ic.analytics_enabled == True

#         ic.analytics_sample_rate = 0.5
#         assert ic.analytics_sample_rate == 0.5

#         assert ic.get_analytics_sample_rate() == 1

#         assert len(warns) == 1
#         warning_message = str(warns[0].message)
#         assert "analytics_sample_rate is deprecated" in warning_message
#         assert "The method currently returns 1 always" in warning_message
#         assert "will be removed in version '4.0.0'" in warning_message