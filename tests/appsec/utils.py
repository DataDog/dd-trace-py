import contextlib
import sys
import typing

from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._processor import AppSecSpanProcessor
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
    _addresses_store.append(env.waf_addresses)
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
    span_type: str = SpanTypes.WEB,
    ip_addr: typing.Optional[str] = None,
    headers_case_sensitive: bool = False,
    headers: typing.Optional[dict[str, str]] = None,
    block_request_callable: typing.Optional[typing.Callable[[], bool]] = None,
    service: typing.Optional[str] = None,
    config=None,
    rc_payload=None,
) -> typing.Iterator[Span]:
    with override_global_config(config) if config else contextlib.nullcontext():
        if tracer is None:
            tracer = default_tracer
        if config:
            # Hack: need to pass an argument to configure so that the processors are recreated
            tracer._span_aggregator.writer._api_version = "v0.4"
            tracer._recreate()
        patch_for_waf_addresses()
        if rc_payload:
            processor = AppSecSpanProcessor._instance
            if processor:
                processor._update_rules([], rc_payload)
        with (
            core.context_with_data(
                "test.asm",
                remote_addr=ip_addr,
                headers_case_sensitive=headers_case_sensitive,
                headers=headers,
                block_request_callable=block_request_callable,
                service=service,
            ),
            tracer.trace(span_name or "test", span_type=span_type, service=service) as span,
        ):
            yield span
        unpatch_for_waf_addresses()


def is_blocked(span: Span) -> bool:
    return span.get_tag("appsec.blocked") == "true" and span.get_tag("appsec.event") == "true"


def assert_nested_request_isolated(calls, nested_url):
    """Check that the request to nested_url had its own subcontext and the outer request kept one.

    calls holds (rule_type, SSRF url, subcontext holder) for each SSRF WAF call.
    """
    nested_holder = next(holder for _, url, holder in calls if url == nested_url)
    assert nested_holder is not None, calls

    nested_calls = [rule_type for rule_type, _, holder in calls if holder is nested_holder]
    assert nested_calls == [EXPLOIT_PREVENTION.TYPE.SSRF_REQ, EXPLOIT_PREVENTION.TYPE.SSRF_RES], calls

    outer = [(rule_type, holder) for rule_type, _, holder in calls if holder is not nested_holder]
    outer_holder = outer[0][1] if outer else None
    assert outer_holder is not None, calls
    assert all(holder is outer_holder for _, holder in outer), calls
    outer_calls = {rule_type for rule_type, _ in outer}
    assert outer_calls == {EXPLOIT_PREVENTION.TYPE.SSRF_REQ, EXPLOIT_PREVENTION.TYPE.SSRF_RES}, calls


@contextlib.contextmanager
def local_http_server():
    """Serve /source as a redirect to /target and every other path as a JSON body, on a free port."""
    from http.server import BaseHTTPRequestHandler
    from http.server import ThreadingHTTPServer
    import threading

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/source":
                self.send_response(302)
                self.send_header("Location", "/target")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
