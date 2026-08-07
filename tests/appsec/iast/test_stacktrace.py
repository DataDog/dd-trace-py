#!/usr/bin/env python3

import pytest

from ddtrace.appsec._iast._handlers import _on_asgi_finalize_response
from ddtrace.appsec._shared._stacktrace import get_info_frame


def test_stacktrace():
    file, line, function, class_ = get_info_frame()
    import traceback

    traceback.print_stack()
    assert file is not None
    assert file.endswith("test_stacktrace.py")
    assert line is not None
    assert function == "test_stacktrace"
    assert class_ is not None


async def test_stacktrace_async():
    async def _inner():
        return get_info_frame()

    file, line, function, class_ = await _inner()
    assert file is not None
    assert file.endswith("test_stacktrace.py")
    assert line is not None
    assert function == "_inner"
    assert class_ is not None


async def test_stacktrace_async_no_relevant_frame():
    """
    In the absence of any non-ddtrace and non-stdlib frame in the stacktrace, no frame is returned.
    (And no exception is raised).
    """
    import asyncio

    file, line, function, class_ = await asyncio.to_thread(get_info_frame)
    assert file is None
    assert line is None
    assert function is None
    assert class_ is None


@pytest.mark.parametrize("body", ["stacktrace", b"stacktrace"])
def test_asgi_finalize_response_checks_text_and_bytes(mocker, body):
    mocker.patch("ddtrace.appsec._iast._handlers.is_iast_request_enabled", return_value=True)
    check_stacktrace = mocker.patch("ddtrace.appsec._iast.taint_sinks.stacktrace_leak.iast_check_stacktrace_leak")

    _on_asgi_finalize_response(body, None)

    check_stacktrace.assert_called_once_with("stacktrace")
