from __future__ import annotations

import re
import typing as t

from ddtrace.testing.internal.tracer_api import rand64bits


if t.TYPE_CHECKING:
    from ddtrace.trace import Span


DDTESTOPT_ROOT_SPAN_RESOURCE = "testing_root_span"


def _gen_item_id() -> int:
    # We use ddtrace's random number generator to avoid being affected by pytest-randomly.
    return rand64bits()


def asbool(value: t.Union[str, bool, None]) -> bool:
    if value is None:
        return False

    if isinstance(value, bool):
        return value

    return value.lower() in ("true", "1")


def ensure_text(s: t.Any) -> str:
    if isinstance(s, str):
        return s
    if isinstance(s, bytes):
        return s.decode("utf-8", errors="ignore")
    return str(s)


_RE_URL = re.compile(r"(https?://|ssh://)[^/]*@")


def _filter_sensitive_info(url: t.Optional[str]) -> t.Optional[str]:
    return _RE_URL.sub("\\1", url) if url is not None else None


class TestContext(t.Protocol):
    span_id: int
    trace_id: int

    def get_tags(self) -> t.Dict[str, str]: ...

    def get_metrics(self) -> t.Dict[str, float]: ...


class PlainTestContext(TestContext):
    def __init__(self, span_id: t.Optional[int] = None, trace_id: t.Optional[int] = None):
        self.span_id = span_id or _gen_item_id()
        self.trace_id = trace_id or _gen_item_id()

    def get_tags(self) -> t.Dict[str, str]:
        return {}

    def get_metrics(self) -> t.Dict[str, float]:
        return {}


class DDTraceTestContext(TestContext):
    def __init__(self, span: Span):
        self.trace_id = span.trace_id % (1 << 64)
        self.span_id = span.span_id % (1 << 64)
        self._span = span

    def get_tags(self) -> t.Dict[str, str]:
        # DEV: in ddtrace < 4.x, key names can be bytes.
        return {ensure_text(k): v for k, v in self._span.get_tags().items()}

    def get_metrics(self) -> t.Dict[str, float]:
        # DEV: in ddtrace < 4.x, key names can be bytes.
        return {ensure_text(k): v for k, v in self._span.get_metrics().items()}
