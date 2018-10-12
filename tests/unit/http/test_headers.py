import pytest
from ddtrace import tracer, Span
from ddtrace.http import store_request_headers, store_response_headers


class TestHeaders(object):

    @pytest.fixture()
    def span(self):
        yield Span(tracer, 'some_span')

    def test_store_multiple_request_headers_as_dict(self, span):
        store_request_headers({
            'Content-Type': 'some;value',
            'Max-Age': 'some;other;value',
        }, span, ['*'])
        assert span.get_tag('http.request.headers.content_type') == 'some;value'
        assert span.get_tag('http.request.headers.max_age') == 'some;other;value'

    def test_store_multiple_response_headers_as_dict(self, span):
        store_response_headers({
            'Content-Type': 'some;value',
            'Max-Age': 'some;other;value',
        }, span, ['*'])
        assert span.get_tag('http.response.headers.content_type') == 'some;value'
        assert span.get_tag('http.response.headers.max_age') == 'some;other;value'

    def test_numbers_in_headers_names_are_allowed(self, span):
        store_response_headers({
            'Content-Type123': 'some;value',
        }, span, ['*'])
        assert span.get_tag('http.response.headers.content_type123') == 'some;value'

    def test_blocs_of_non_letters_and_digits_to_underscore(self, span):
        store_response_headers({
            'Content----T_%%y&%$pe': 'some;value',
        }, span, ['*'])
        assert span.get_tag('http.response.headers.content_t_y_pe') == 'some;value'

    def test_key_trim_leading_trailing_spaced(self, span):
        store_response_headers({
            '   Content-Type   ': 'some;value',
        }, span, ['*'])
        assert span.get_tag('http.response.headers.content_type') == 'some;value'

    def test_value_not_trim_leading_trailing_spaced(self, span):
        store_response_headers({
            'Content-Type': '   some;value   ',
        }, span, ['*'])
        assert span.get_tag('http.response.headers.content_type') == '   some;value   '

    def test_no_whitelist(self, span):
        store_response_headers({
            'Content-Type': 'some;value',
        }, span, [])
        assert span.get_tag('http.response.headers.content_type') is None

    def test_whitelist_all(self, span):
        store_response_headers({
            'Content-Type': 'some;value',
        }, span, ['*'])
        assert span.get_tag('http.response.headers.content_type') == 'some;value'

    def test_whitelist_exact(self, span):
        store_response_headers({
            'Content-Type': 'some;value',
        }, span, ['content-type'])
        assert span.get_tag('http.response.headers.content_type') == 'some;value'

    def test_whitelist_match(self, span):
        store_response_headers({
            'Content-Type': 'some;value',
        }, span, ['conten*ype'])
        assert span.get_tag('http.response.headers.content_type') == 'some;value'

    def test_whitelist_case_insensitive(self, span):
        store_response_headers({
            'ContENt-Type': 'some;value',
        }, span, ['conTen*ypE'])
        assert span.get_tag('http.response.headers.content_type') == 'some;value'
