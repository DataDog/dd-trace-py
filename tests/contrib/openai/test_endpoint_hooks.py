"""Tests for _EndpointHook.handle_request async parse handling."""
import asyncio
import warnings

import mock
import pytest

from ddtrace.contrib.internal.openai._endpoint_hooks import _EndpointHook


class _FakeSyncAPIResponse:
    """Simulates a sync APIResponse where parse() returns a plain object."""

    def __init__(self, parsed):
        self._parsed = parsed

    def parse(self):
        return self._parsed


class _FakeAsyncAPIResponse:
    """Simulates an async APIResponse where parse() returns a coroutine (openai>=2.25.0)."""

    def __init__(self, parsed):
        self._parsed = parsed

    async def parse(self):
        return self._parsed


def _run_hook(hook, resp, error=None):
    """Drive the handle_request generator and return the result."""
    pin = mock.MagicMock()
    integration = mock.MagicMock()
    instance = mock.MagicMock()
    span = mock.MagicMock()

    gen = hook.handle_request(pin, integration, instance, span, (), {})
    gen.send(None)  # advance to yield
    try:
        gen.send((resp, error))
    except StopIteration as e:
        return e.value, span
    return None, span


def test_sync_raw_response_parse():
    """Sync APIResponse.parse() returns a plain object -- tags should be recorded from parsed response."""
    hook = _EndpointHook()
    hook._response_attrs = ("model",)

    parsed = mock.MagicMock()
    parsed.model = "gpt-4"
    resp = _FakeSyncAPIResponse(parsed)

    result, span = _run_hook(hook, resp)

    assert result is resp
    span._set_tag_str.assert_any_call("openai.response.model", "gpt-4")


def test_async_raw_response_parse_no_warning():
    """AsyncAPIResponse.parse() returns a coroutine -- should not emit 'coroutine never awaited' warning."""
    hook = _EndpointHook()
    hook._response_attrs = ("model",)

    resp = _FakeAsyncAPIResponse(mock.MagicMock(model="gpt-4"))

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result, span = _run_hook(hook, resp)

    coroutine_warnings = [x for x in w if "never awaited" in str(x.message)]
    assert len(coroutine_warnings) == 0
    assert result is resp


def test_regular_response_without_parse():
    """Normal response without parse attribute -- should be passed directly to _record_response."""
    hook = _EndpointHook()
    hook._response_attrs = ("model",)

    resp = mock.MagicMock(spec=["model"])
    resp.model = "gpt-4"

    result, span = _run_hook(hook, resp)

    assert result is resp
    span._set_tag_str.assert_any_call("openai.response.model", "gpt-4")
