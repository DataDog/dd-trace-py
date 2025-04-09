import contextlib
import sys
import typing

from ddtrace.appsec import _asm_request_context
from ddtrace.ext import SpanTypes
import ddtrace.internal.core as core
from ddtrace.trace import Span
from ddtrace.trace import tracer as default_tracer
from tests.utils import override_global_config
from tests.utils import remote_config_build_payload as build_payload  # noqa: F401


class Either:
    def __init__(self, *possibilities):
        self.possibilities = possibilities

    def __eq__(self, other):
        if other not in self.possibilities:
            print(f"Either: Expected {other} to be in {self.possibilities}", file=sys.stderr, flush=True)
            return False
        return True


_init_finalize = _asm_request_context.finalize_asm_env
_addresses_store = []


def finalize_wrapper(env):
    print(f"Finalizing ASM env: {env}", file=sys.stderr, flush=True)
    _addresses_store.append(env.waf_addresses)
    print(f"Addresses store: {_addresses_store}", file=sys.stderr, flush=True)
    _init_finalize(env)


def patch_for_waf_addresses():
    _addresses_store.clear()
    _asm_request_context.finalize_asm_env = finalize_wrapper


def unpatch_for_waf_addresses():
    _asm_request_context.finalize_asm_env = _init_finalize


def get_waf_addresses(address: str) -> typing.Optional[typing.Any]:
    """
    Returns the last WAF addresses store.
    """
    if _addresses_store:
        return _addresses_store[-1].get(address, None)
    return None


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
        if config:
            # Hack: need to pass an argument to configure so that the processors are recreated
            tracer._writer._api_version = "v0.4"
            tracer._recreate()
        patch_for_waf_addresses()
        with core.context_with_data(
            "test.asm",
            remote_addr=ip_addr,
            headers_case_sensitive=headers_case_sensitive,
            headers=headers,
            block_request_callable=block_request_callable,
            service=service,
        ), tracer.trace(span_name or "test", span_type=SpanTypes.WEB, service=service) as span:
            yield span
        unpatch_for_waf_addresses()


def is_blocked(span: Span) -> bool:
    return span.get_tag("appsec.blocked") == "true" and span.get_tag("appsec.event") == "true"
