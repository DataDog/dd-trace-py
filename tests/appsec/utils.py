import contextlib
import sys
import typing

from ddtrace import tracer as default_tracer
from ddtrace._trace.span import Span
from ddtrace.ext import SpanTypes
import ddtrace.internal.core as core
from ddtrace.settings.asm import config as asm_config
from tests.utils import override_global_config


class Either:
    def __init__(self, *possibilities):
        self.possibilities = possibilities

    def __eq__(self, other):
        if other not in self.possibilities:
            print(f"Either: Expected {other} to be in {self.possibilities}", file=sys.stderr, flush=True)
            return False
        return True


@contextlib.contextmanager
def asm_context(
    tracer=None,
    span_name: str = "",
    ip_addr: typing.Optional[str] = None,
    headers_case_sensitive: bool = False,
    headers: typing.Optional[typing.Dict[str, str]] = None,
    block_request_callable: typing.Optional[typing.Callable[[], bool]] = None,
    service: typing.Optional[str] = None,
    config=None,
) -> typing.Iterator[Span]:
    with override_global_config(config) if config else contextlib.nullcontext():
        if tracer is None:
            tracer = default_tracer
        if asm_config._asm_enabled:
            tracer._asm_enabled = True
        if config:
            tracer.configure(api_version="v0.4")

        with core.context_with_data(
            "test.asm",
            remote_addr=ip_addr,
            headers_case_sensitive=headers_case_sensitive,
            headers=headers,
            block_request_callable=block_request_callable,
            service=service,
        ), tracer.trace(span_name or "test", span_type=SpanTypes.WEB, service=service) as span:
            yield span


def is_blocked(span: Span) -> bool:
    return span.get_tag("appsec.blocked") == "true" and span.get_tag("appsec.event") == "true"
