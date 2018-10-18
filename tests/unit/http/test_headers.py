import pytest

from ddtrace import tracer, Span
from ddtrace.http import store_request_headers, store_response_headers
from ddtrace.settings import Config, IntegrationConfig


class TestHeaders(object):

    @pytest.fixture()
    def span(self):
        yield Span(tracer, 'some_span')

    @pytest.fixture()
    def config(self):
        yield Config

    @pytest.fixture()
    def integration_config(self, config):
        yield IntegrationConfig(config)

    def test_it_does_not_break_if_no_headers(self, span):
        store_request_headers(None, span, ['*'])
        store_response_headers(None, span, ['*'])

    def test_it_does_not_break_if_headers_are_not_a_dict(self, span):
        store_request_headers(list(), span, ['*'])
        store_response_headers(list(), span, ['*'])

    def test_store_multiple_request_headers_as_dict(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        integration_config.http.trace_headers('.*')
        store_request_headers({
            'Content-Type': 'some;value',
            'Max-Age': 'some;other;value',
        }, span, integration_config)
        assert span.get_tag('http.request.headers.content_type') == 'some;value'
        assert span.get_tag('http.request.headers.max_age') == 'some;other;value'

    def test_store_multiple_response_headers_as_dict(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        integration_config.http.trace_headers('.*')
        store_response_headers({
            'Content-Type': 'some;value',
            'Max-Age': 'some;other;value',
        }, span, integration_config)
        assert span.get_tag('http.response.headers.content_type') == 'some;value'
        assert span.get_tag('http.response.headers.max_age') == 'some;other;value'

    def test_numbers_in_headers_names_are_allowed(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        integration_config.http.trace_headers('.*')
        store_response_headers({
            'Content-Type123': 'some;value',
        }, span, integration_config)
        assert span.get_tag('http.response.headers.content_type123') == 'some;value'

    def test_blocks_of_non_letters_and_digits_to_underscore(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        integration_config.http.trace_headers('.*')
        store_response_headers({
            'Content----T_%%y&%$pe': 'some;value',
        }, span, integration_config)
        assert span.get_tag('http.response.headers.content_t_y_pe') == 'some;value'

    def test_key_trim_leading_trailing_spaced(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        integration_config.http.trace_headers('.*')
        store_response_headers({
            '   Content-Type   ': 'some;value',
        }, span, integration_config)
        assert span.get_tag('http.response.headers.content_type') == 'some;value'

    def test_value_not_trim_leading_trailing_spaced(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        integration_config.http.trace_headers('.*')
        store_response_headers({
            'Content-Type': '   some;value   ',
        }, span, integration_config)
        assert span.get_tag('http.response.headers.content_type') == '   some;value   '

    def test_no_whitelist(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        store_response_headers({
            'Content-Type': 'some;value',
        }, span, integration_config)
        assert span.get_tag('http.response.headers.content_type') is None

    def test_whitelist_all(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        integration_config.http.trace_headers('.*')
        store_response_headers({
            'Content-Type': 'some;value',
        }, span, integration_config)
        assert span.get_tag('http.response.headers.content_type') == 'some;value'

    def test_whitelist_exact(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        integration_config.http.trace_headers('content-type')
        store_response_headers({
            'Content-Type': 'some;value',
        }, span, integration_config)
        assert span.get_tag('http.response.headers.content_type') == 'some;value'

    def test_whitelist_match(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        integration_config.http.trace_headers('conten.*ype')
        store_response_headers({
            'Content-Type': 'some;value',
            'No-Match': 'some;other;value',
        }, span, integration_config)
        assert span.get_tag('http.response.headers.content_type') == 'some;value'

    def test_whitelist_case_insensitive(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        integration_config.http.trace_headers('conTen.*ypE')
        store_response_headers({
            'ContENt-Type': 'some;value',
        }, span, integration_config)
        assert span.get_tag('http.response.headers.content_type') == 'some;value'
