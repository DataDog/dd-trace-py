from typing import Any

from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_active_asm_context
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import open_rasp_subcontext_scope
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._rasp import _must_block
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.logger import get_logger
from ddtrace.internal.wrapping.context import WrappingContext
from ddtrace.internal.wrapping.hooks import try_unwrap_context
from ddtrace.internal.wrapping.hooks import try_wrap_context


log = get_logger(__name__)


class _SsrfWebbrowserOpen(WrappingContext):
    """RASP SSRF analysis around webbrowser.open.

    There is no response to inspect - open returns a bool - so this is request-side only.
    """

    def __enter__(self) -> "_SsrfWebbrowserOpen":
        super().__enter__()
        try:
            self._handle_enter()
        except Exception:
            # A block is BaseException-derived and still propagates; anything else is our bug and
            # must not reach the application.
            log.debug("Error handling SSRF instrumentation enter", exc_info=True)
        return self

    def _handle_enter(self) -> None:
        if not get_rasp_capability("ssrf"):
            return
        if get_active_asm_context() is None:
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, False)
            return

        # Read by name: webbrowser.open is open(url, new=0, autoraise=True), so the URL is the
        # first argument, and a positional read of the second one picks up new instead.
        url: Any = self.__frame__.f_locals.get("url")
        if not (isinstance(url, str) and url):
            return

        # open_rasp_subcontext_scope is documented to be called from a per-outgoing-request core
        # context; without one, every call in the request would share a single subcontext.
        with core.context_with_data("url_open_analysis", full_url=url):
            open_rasp_subcontext_scope()
            res = call_waf_callback(
                {EXPLOIT_PREVENTION.ADDRESS.SSRF: url},
                crop_trace=self.__wrapped__.__code__.co_name,
                rule_type=EXPLOIT_PREVENTION.TYPE.SSRF,
            )
        # Raised outside the scope so it is released before the block propagates.
        if res and _must_block(res.actions):
            raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, url)


def patch() -> None:
    try_wrap_context("webbrowser", "open", _SsrfWebbrowserOpen)


def unpatch() -> None:
    try_unwrap_context("webbrowser", "open")
