"""
Tests for the async streaming span lifecycle fix in the OpenAI integration.

Bug: _EndpointHook.handle_request (sync generator) calls resp.parse() on
AsyncAPIResponse, which returns an unawaited coroutine.  The stream is never
wrapped in TracedAsyncStream, so the span is never finished.

Fix: pre-parse AsyncAPIResponse in async callers before sending into the sync
generator, and inject the traced stream back into the response's parse cache.
"""

import asyncio

import pytest

from ddtrace.contrib.internal.openai.patch import _inject_into_parse_cache
from ddtrace.contrib.internal.openai.patch import _maybe_preparse_async_response


# ---------------------------------------------------------------------------
# _inject_into_parse_cache
# ---------------------------------------------------------------------------


class _FakeResponseV2:
    """Mimics OpenAI SDK >=2.0 AsyncAPIResponse (uses _parsed_by_type)."""

    def __init__(self):
        self._parsed_by_type = {}
        self._cast_to = "ResponseType"


class _FakeResponseV1:
    """Mimics OpenAI SDK 1.x AsyncAPIResponse (uses _parsed)."""

    def __init__(self):
        self._parsed = None


class _PlainObject:
    """Object without any parse cache attributes."""

    pass


@pytest.mark.parametrize(
    "resp_factory,check",
    [
        pytest.param(
            lambda: _FakeResponseV2(),
            lambda resp, val: resp._parsed_by_type[resp._cast_to] is val,
            id="sdk_v2_parsed_by_type",
        ),
        pytest.param(
            lambda: (r := _FakeResponseV2(), setattr(r, "_parsed_by_type", {"ResponseType": "old"}))[0],
            lambda resp, val: resp._parsed_by_type["ResponseType"] is val,
            id="sdk_v2_overwrites_existing",
        ),
        pytest.param(
            lambda: _FakeResponseV1(),
            lambda resp, val: resp._parsed is val,
            id="sdk_v1_parsed",
        ),
    ],
)
def test_inject_into_parse_cache(resp_factory, check):
    resp = resp_factory()
    sentinel = object()
    _inject_into_parse_cache(resp, sentinel)
    assert check(resp, sentinel)


def test_inject_into_parse_cache_noop_without_cache_attrs():
    resp = _PlainObject()
    _inject_into_parse_cache(resp, object())


# ---------------------------------------------------------------------------
# _maybe_preparse_async_response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preparse_awaits_async_parse():
    """When resp.parse() is async, _maybe_preparse should await it."""
    stream = object()

    class _Resp:
        async def parse(self):
            return stream

    result = await _maybe_preparse_async_response(_Resp(), None)
    assert result is stream


@pytest.mark.asyncio
async def test_preparse_returns_sync_parse():
    """When resp.parse() is sync, _maybe_preparse should return its value."""
    stream = object()

    class _Resp:
        def parse(self):
            return stream

    result = await _maybe_preparse_async_response(_Resp(), None)
    assert result is stream


@pytest.mark.asyncio
async def test_preparse_noop_without_parse():
    """Without a parse attr, _maybe_preparse returns resp unchanged."""
    resp = object()
    result = await _maybe_preparse_async_response(resp, None)
    assert result is resp


@pytest.mark.asyncio
async def test_preparse_noop_on_error():
    """When err is set, _maybe_preparse returns resp unchanged."""

    class _Resp:
        async def parse(self):
            raise AssertionError("should not be called")

    resp = _Resp()
    result = await _maybe_preparse_async_response(resp, RuntimeError("fail"))
    assert result is resp


@pytest.mark.asyncio
async def test_preparse_noop_on_error_returns_original():
    """When err is not None, resp should be returned as-is without calling parse."""
    resp = _PlainObject()
    resp.parse = lambda: (_ for _ in ()).throw(AssertionError("should not be called"))
    result = await _maybe_preparse_async_response(resp, RuntimeError("original error"))
    assert result is resp


@pytest.mark.asyncio
async def test_preparse_swallows_parse_failure():
    """If parse() raises, _maybe_preparse falls back to the original resp."""

    class _Resp:
        def parse(self):
            raise ValueError("bad payload")

    resp = _Resp()
    result = await _maybe_preparse_async_response(resp, None)
    assert result is resp


@pytest.mark.asyncio
async def test_preparse_swallows_async_parse_failure():
    """If an async parse() raises, _maybe_preparse falls back to resp."""

    class _Resp:
        async def parse(self):
            raise ValueError("bad async payload")

    resp = _Resp()
    result = await _maybe_preparse_async_response(resp, None)
    assert result is resp


@pytest.mark.asyncio
async def test_preparse_noop_when_resp_is_none():
    result = await _maybe_preparse_async_response(None, None)
    assert result is None
