import pytest

from ddtrace._trace.processor.resource_renaming import ResourceRenamingProcessor
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.trace import Context
from ddtrace.trace import Span
from tests.utils import override_global_config


class TestResourceRenaming:
    @pytest.mark.parametrize(
        "elem,expected",
        [
            # Integer patterns
            ("123", "{param:int}"),
            ("10", "{param:int}"),
            ("12345", "{param:int}"),
            ("0", "0"),
            ("01", "01"),
            # Integer ID patterns
            ("123.456", "{param:int_id}"),
            ("123-456-789", "{param:int_id}"),
            ("0123", "{param:int_id}"),
            # Hex patterns (require at least one digit)
            ("123ABC", "{param:hex}"),
            ("a1b2c3", "{param:hex}"),
            ("abcdef", "abcdef"),
            ("ABCDEF", "ABCDEF"),
            ("abcde", "abcde"),
            # Hex ID patterns
            ("123.ABC", "{param:hex_id}"),
            ("a1b2-c3d4", "{param:hex_id}"),
            ("abc-def", "abc-def"),
            # String patterns
            ("this_is_a_very_long_string", "{param:str}"),
            ("with%special&chars", "{param:str}"),
            ("email@domain.com", "{param:str}"),
            ("file.with.dots", "file.with.dots"),
            # No match cases
            ("users", "users"),
            ("short", "short"),
            ("xyz123", "xyz123"),
        ],
    )
    def test_compute_simplified_endpoint_path_element(self, elem, expected):
        processor = ResourceRenamingProcessor()
        result = processor._compute_simplified_endpoint_path_element(elem)
        assert result == expected

    @pytest.mark.parametrize(
        "url,expected",
        [
            # Basic cases
            ("", "/"),
            ("http://example.com", "/"),
            ("http://example.com/", "/"),
            ("/users", "/users"),
            ("https://example.com/users", "/users"),
            # Query and fragment handling
            ("http://example.com/api/users?id=123", "/api/users"),
            ("https://example.com/users/123#section", "/users/{param:int}"),
            ("https://example.com/users/123?filter=active#top", "/users/{param:int}"),
            # Parameter replacement
            ("/users/123", "/users/{param:int}"),
            ("/users/5", "/users/5"),
            ("/users/0123", "/users/{param:int_id}"),
            ("/items/123-456", "/items/{param:int_id}"),
            ("/commits/abc123", "/commits/{param:hex}"),
            ("/sessions/deadbeef", "/sessions/deadbeef"),
            ("/items/abc123-def", "/items/{param:hex_id}"),
            ("/files/verylongfilename12345", "/files/{param:str}"),
            ("/users/user@example", "/users/{param:str}"),
            # Path limits and edge cases
            ("/a/b/c/d/e/f/g/h/i/j/k", "/a/b/c/d/e/f/g/h"),
            ("/api//v1///users//123", "/api/v1/users/{param:int}"),
            ("///////////////////////", "/"),
            # Complex mixed cases
            (
                "/api/v2/users/123/posts/abc123/comments/hello%20world",
                "/api/v2/users/{param:int}/posts/{param:hex}/comments/{param:str}",
            ),
            (
                "/12/123-456/abc123/abc-def-123/longstringthathastoomanycharacters",
                "/{param:int}/{param:int_id}/{param:hex}/{param:hex_id}/{param:str}",
            ),
            # Error cases
            (None, "/"),
        ],
    )
    def test_compute_simplified_endpoint(self, url, expected):
        processor = ResourceRenamingProcessor()
        result = processor._compute_simplified_endpoint(url)
        assert result == expected

    def test_processor_with_route(self):
        processor = ResourceRenamingProcessor()
        span = Span("test", context=Context(), span_type=SpanTypes.WEB)
        span.set_tag(http.ROUTE, "/api/users/{id}")
        span.set_tag(http.URL, "https://example.com/api/users/123")

        processor.on_span_finish(span)
        assert span.get_tag(http.ENDPOINT) is None

    def test_processor_without_route(self):
        processor = ResourceRenamingProcessor()
        span = Span("test", context=Context(), span_type=SpanTypes.WEB)
        span.set_tag(http.URL, "https://example.com/api/users/123")

        processor.on_span_finish(span)
        assert span.get_tag(http.ENDPOINT) == "/api/users/{param:int}"

    def test_processor_always_simplified_endpoint(self):
        processor = ResourceRenamingProcessor()
        with override_global_config(dict(_trace_resource_renaming_always_simplified_endpoint=True)):
            span = Span("test", context=Context(), span_type=SpanTypes.WEB)
            span.set_tag(http.ROUTE, "/api/users/{id}")
            span.set_tag(http.URL, "https://example.com/api/users/123")

            processor.on_span_finish(span)
        # Should use simplified endpoint even when route exists
        assert span.get_tag(http.ENDPOINT) == "/api/users/{param:int}"
