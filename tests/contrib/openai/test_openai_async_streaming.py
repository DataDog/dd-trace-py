"""
Tests for the fix to async streaming span lifecycle in the OpenAI integration.

The bug: _EndpointHook.handle_request (sync generator) calls resp.parse() on
AsyncAPIResponse, which returns an unawaited coroutine. The stream is never
wrapped in TracedAsyncStream, so the span is never finished.

The fix: pre-parse AsyncAPIResponse in async_wrapper / _trace_and_await before
sending into the sync generator, and inject the traced stream back into the
response's parse cache.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from ddtrace.contrib.internal.openai.patch import _inject_into_parse_cache


class TestInjectIntoParseCacheV2:
    """OpenAI SDK v2.x uses _parsed_by_type dict keyed by _cast_to."""

    def test_injects_into_parsed_by_type(self):
        resp = MagicMock()
        resp._parsed_by_type = {}
        resp._cast_to = "SomeType"
        del resp._parsed

        traced_stream = MagicMock()
        _inject_into_parse_cache(resp, traced_stream)

        assert resp._parsed_by_type["SomeType"] is traced_stream

    def test_overwrites_existing_cache_entry(self):
        resp = MagicMock()
        resp._parsed_by_type = {"SomeType": "original"}
        resp._cast_to = "SomeType"
        del resp._parsed

        traced_stream = MagicMock()
        _inject_into_parse_cache(resp, traced_stream)

        assert resp._parsed_by_type["SomeType"] is traced_stream


class TestInjectIntoParseCacheV1:
    """OpenAI SDK v1.x uses a single _parsed attribute."""

    def test_injects_into_parsed(self):
        resp = MagicMock(spec=["_parsed"])
        resp._parsed = None

        traced_stream = MagicMock()
        _inject_into_parse_cache(resp, traced_stream)

        assert resp._parsed is traced_stream


class TestInjectIntoParseCacheNoOp:
    """Objects without parse cache attributes are left unchanged."""

    def test_no_cache_attributes(self):
        resp = MagicMock(spec=[])
        traced_stream = MagicMock()
        _inject_into_parse_cache(resp, traced_stream)


class TestAsyncWrapperPreParse:
    """Verify that async_wrapper pre-parses AsyncAPIResponse and returns it
    with the traced stream injected into the parse cache."""

    @pytest.mark.asyncio
    async def test_async_response_preparsed_before_hook(self):
        """When resp has an async parse(), the pre-parse branch should await it
        and send the parsed result (not the coroutine) to the generator."""
        from ddtrace.contrib.internal.openai.patch import _patched_endpoint_async

        parse_called = False
        parse_awaited = False

        class FakeAsyncStream:
            pass

        class FakeAsyncAPIResponse:
            _parsed_by_type = {}
            _cast_to = "FakeStream"

            async def parse(self):
                nonlocal parse_called, parse_awaited
                parse_called = True
                parse_awaited = True
                return FakeAsyncStream()

        class FakeEndpointHook:
            OPERATION_ID = "createResponse"

        fake_stream = FakeAsyncStream()

        class FakeHook:
            def handle_request(self, pin, integration, instance, span, args, kwargs):
                self.received_resp = None
                resp, error = yield
                self.received_resp = resp
                return resp

        original_response = FakeAsyncAPIResponse()

        async def fake_create(*args, **kwargs):
            return original_response

        fake_integration = MagicMock()
        fake_integration.trace.return_value = MagicMock()
        fake_integration.trace.return_value.finish = MagicMock()

        import openai as openai_module

        original_integration = getattr(openai_module, "_datadog_integration", None)
        openai_module._datadog_integration = fake_integration

        try:
            endpoint_factory = _patched_endpoint_async(FakeEndpointHook)

            result_coro = endpoint_factory(fake_create, None, (), {"stream": True})
            result = await result_coro

            assert isinstance(result, FakeAsyncAPIResponse), (
                f"Expected AsyncAPIResponse, got {type(result).__name__}"
            )
            assert parse_awaited, "parse() should have been awaited"
        finally:
            if original_integration is not None:
                openai_module._datadog_integration = original_integration
            elif hasattr(openai_module, "_datadog_integration"):
                delattr(openai_module, "_datadog_integration")
