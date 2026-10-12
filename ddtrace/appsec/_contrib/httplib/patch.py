from typing import Any
from typing import Callable

from ddtrace.appsec._asm_request_context import _get_asm_context
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import open_rasp_subcontext_scope
from ddtrace.appsec._asm_request_context import should_analyze_body_response
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._contrib.requests.subscribers import APPSEC_REQUESTS_CONTEXT_KEY
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.internal import core


def _wrap_request(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Keep the legacy httplib entry point until its operation-event migration."""
    if core.find_item(APPSEC_REQUESTS_CONTEXT_KEY) is not None:
        # Requests owns this operation; do not resample or republish a URL consumed by urllib3.
        return func(*args, **kwargs)

    if get_rasp_capability("ssrf"):
        url = args[1] if len(args) > 1 else kwargs.get("url")
        valid_url = isinstance(url, str) and bool(url)
        if valid_url and (ctx := _get_asm_context()):
            use_body = should_analyze_body_response(ctx)
            # The legacy context factory is untyped; keep its behavior until httplib is migrated.
            with core.context_with_data(  # type: ignore[no-untyped-call]
                "url_open_analysis", full_url=url, use_body=use_body
            ):
                open_rasp_subcontext_scope()
                response = func(*args, **kwargs)
                if response.__class__.__name__ == "Response" and not (300 <= response.status_code < 400):
                    addresses = {
                        "DOWN_RES_STATUS": str(response.status_code),
                        "DOWN_RES_HEADERS": dict(response.headers),
                    }
                    if use_body:
                        try:
                            addresses["DOWN_RES_BODY"] = response.json()
                        except Exception:
                            pass  # nosec
                    call_waf_callback(addresses, rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES)
                return response
        elif valid_url:
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, False)
    return func(*args, **kwargs)
