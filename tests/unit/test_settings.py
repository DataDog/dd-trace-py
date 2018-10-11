import pytest
from ddtrace.settings import  Config


class TestHttpSettings(object):

    @pytest.fixture
    def config(self):
        return Config()

    def test_empty_if_traced_headers_not_configured(self, config):
        """:type config: Config"""
        assert set() == config.http.get_integration_traced_headers('my_integration')

    def test_can_configure_an_integration_specific_traced_header(self, config):
        """:type config: Config"""
        config.http.trace_headers('some_header', integrations='my_integration')
        assert {'some_header'} == config.http.get_integration_traced_headers('my_integration')
        assert set() == config.http.get_integration_traced_headers('other_integration')

    def test_can_configure_multiple_integrations_specific_traced_header(self, config):
        """:type config: Config"""
        config.http.trace_headers('some_header', integrations=['my_integration', 'my_other_integration'])
        assert {'some_header'} == config.http.get_integration_traced_headers('my_integration')
        assert {'some_header'} == config.http.get_integration_traced_headers('my_other_integration')
        assert set() == config.http.get_integration_traced_headers('disabled_integration')

    def test_can_configure_multiple_integrations_multiple_traced_header(self, config):
        """:type config: Config"""
        config.http.trace_headers('some_header', 'some_other_header',
                                  integrations=['my_integration', 'my_other_integration'])
        assert {'some_header', 'some_other_header'} == \
            config.http.get_integration_traced_headers('my_integration')
        assert {'some_header', 'some_other_header'} == \
            config.http.get_integration_traced_headers('my_other_integration')
        assert set() == config.http.get_integration_traced_headers('disabled_integration')

    def test_integrations_specific_and_global_traced_header_merged(self, config):
        """:type config: Config"""
        config.http.trace_headers('some_header', integrations='my_integration')
        config.http.trace_headers('some_other_header')
        assert {'some_header', 'some_other_header'} == config.http.get_integration_traced_headers('my_integration')

    def test_header_names_are_trimmed(self, config):
        """:type config: Config"""
        config.http.trace_headers('   some   header   ')
        assert {'some   header'} == config.http.get_integration_traced_headers('my_integration')

    def test_header_names_are_lowered(self, config):
        """:type config: Config"""
        config.http.trace_headers('SoMe_HeAdEr')
        assert {'some_header'} == config.http.get_integration_traced_headers('my_integration')

    def test_can_be_called_fluently(self, config):
        """:type config: Config"""
        config.http \
            .trace_headers('global') \
            .trace_headers('integration', integrations='my_integration')
        assert {'global', 'integration'} == config.http.get_integration_traced_headers('my_integration')
        assert {'global'} == config.http.get_integration_traced_headers('disabled_integration')

    def test_headers_names_are_not_duplicated(self, config):
        """:type config: Config"""
        config.http \
            .trace_headers('global') \
            .trace_headers('global') \
            .trace_headers('integration', integrations='my_integration') \
            .trace_headers('integration', integrations='my_integration')
        assert {'global', 'integration'} == config.http.get_integration_traced_headers('my_integration')
        assert {'global'} == config.http.get_integration_traced_headers('disabled_integration')
