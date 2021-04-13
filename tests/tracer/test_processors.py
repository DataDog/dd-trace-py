from typing import Any

import attr
import mock
import pytest

from ddtrace import Span
from ddtrace.internal.processor import SpanProcessor


def test_no_impl():
    @attr.s
    class BadProcessor(SpanProcessor):
        pass

    with pytest.raises(TypeError):
        BadProcessor()


def test_default_post_init():
    @attr.s
    class MyProcessor(SpanProcessor):
        def on_span_start(self, span):  # type: (Span) -> None
            pass

        def on_span_finish(self, data):  # type: (Any) -> Any
            pass

    with mock.patch("ddtrace.internal.processor.log") as log:
        p = MyProcessor()

    calls = [
        mock.call("initialized processor %r", p),
    ]
    log.debug.assert_has_calls(calls)
