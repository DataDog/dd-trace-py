import pytest

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

    def test_trace_headers_blacklist_case_insensitive(self):
        http_config = HttpConfig()
        http_config.trace_headers('.*', blacklist=['some_other_header'])
        assert http_config.header_is_traced('some_header')
        assert not http_config.header_is_traced('sOmE_oThEr_HeAdEr')

    def test_trace_multiple_headers(self):
        http_config = HttpConfig()
        http_config.trace_headers(['some_header_1', 'some_header_2'])
        assert http_config.header_is_traced('some_header_1')
        assert http_config.header_is_traced('some_header_2')
        assert not http_config.header_is_traced('some_header_3')

    def test_trace_blacklist(self):
        http_config = HttpConfig()
        http_config.trace_headers(['.*'], blacklist=['some_header_2'])
        assert http_config.header_is_traced('some_header_1')
        assert not http_config.header_is_traced('some_header_2')

    def test_invalid_regex_do_not_raise_exception(self):
        http_config = HttpConfig()
        http_config.trace_headers('[[[[[[[')

    def test_is_header_tracing_configured(self):
        http_config = HttpConfig()
        assert not http_config.is_header_tracing_configured
        http_config.trace_headers('some_header')
        assert http_config.is_header_tracing_configured


class TestIntegrationConfig(object):

    def test_is_a_dict(self):
        integration_config = IntegrationConfig(Config())
        assert isinstance(integration_config, dict)

    def test_allow_configuring_http(self):
        global_config = Config()
        integration_config = IntegrationConfig(global_config)
        integration_config.http.trace_headers('integration_header')
        assert integration_config.http.header_is_traced('integration_header')
        assert not integration_config.http.header_is_traced('other_header')
