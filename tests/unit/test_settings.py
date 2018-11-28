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


class TestIntegrationConfig(object):

    def test_is_a_dict(self):
        integration_config = IntegrationConfig(Config())
        assert isinstance(integration_config, dict)

    def test_allow_item_access(self):
        config = IntegrationConfig(Config())
        config['setting'] = 'value'

        # Can be accessed both as item and attr accessor
        assert config.setting == 'value'
        assert config['setting'] == 'value'

    def test_allow_attr_access(self):
        config = IntegrationConfig(Config())
        config.setting = 'value'

        # Can be accessed both as item and attr accessor
        assert config.setting == 'value'
        assert config['setting'] == 'value'

    def test_allow_both_access(self):
        config = IntegrationConfig(Config())

        config.setting = 'value'
        assert config['setting'] == 'value'
        assert config.setting == 'value'

        config['setting'] = 'new-value'
        assert config.setting == 'new-value'
        assert config['setting'] == 'new-value'

    def test_allow_configuring_http(self):
        global_config = Config()
        integration_config = IntegrationConfig(global_config)
        integration_config.http.trace_headers('integration_header')
        assert integration_config.http.header_is_traced('integration_header')
        assert not integration_config.http.header_is_traced('other_header')

    def test_allow_exist_both_global_and_integration_config(self):
        global_config = Config()
        integration_config = IntegrationConfig(global_config)

        global_config.trace_headers('global_header')
        assert integration_config.header_is_traced('global_header')

        integration_config.http.trace_headers('integration_header')
        assert integration_config.header_is_traced('integration_header')
        assert not integration_config.header_is_traced('global_header')
        assert not global_config.header_is_traced('integration_header')
