"""
This module contains utility functions for writing ddtrace integrations.
"""

from __future__ import annotations

from collections import deque
from typing import Any  # noqa:F401
from typing import Callable  # noqa:F401
from typing import Generator  # noqa:F401
from typing import Iterator  # noqa:F401
from typing import Mapping  # noqa:F401
from typing import MutableMapping  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Sequence  # noqa:F401
from typing import Union  # noqa:F401
from urllib import parse

import wrapt

from ddtrace._trace.http_meta import IP_PATTERNS  # noqa:F401
from ddtrace._trace.http_meta import NORMALIZE_PATTERN  # noqa:F401
from ddtrace._trace.http_meta import REQUEST  # noqa:F401
from ddtrace._trace.http_meta import RESPONSE  # noqa:F401
from ddtrace._trace.http_meta import _get_request_header_client_ip  # noqa:F401
from ddtrace._trace.http_meta import _get_request_header_referrer_host  # noqa:F401
from ddtrace._trace.http_meta import _parse_ip_header  # noqa:F401
from ddtrace._trace.http_meta import _sanitized_url  # noqa:F401
from ddtrace._trace.http_meta import _set_http_meta  # noqa:F401
from ddtrace._trace.http_meta import _set_http_meta_otel  # noqa:F401
from ddtrace._trace.http_meta import _store_headers  # noqa:F401
from ddtrace._trace.http_meta import _store_request_headers  # noqa:F401
from ddtrace._trace.http_meta import _store_response_headers  # noqa:F401
from ddtrace._trace.http_meta import set_http_meta  # noqa:F401
from ddtrace._trace.pin import Pin
from ddtrace._trace.propagation import activate_distributed_headers  # noqa:F401
from ddtrace._trace.propagation import distributed_tracing_enabled  # noqa:F401
from ddtrace._trace.span import Span
from ddtrace.constants import _ORIGIN_KEY
from ddtrace.contrib._events.web_framework import WebFrameworkEvents
from ddtrace.contrib.internal.trace_utils_base import USER_AGENT_PATTERNS  # noqa:F401
from ddtrace.contrib.internal.trace_utils_base import _get_header_value_case_insensitive  # noqa:F401
from ddtrace.contrib.internal.trace_utils_base import _get_request_header_user_agent  # noqa:F401
from ddtrace.contrib.internal.trace_utils_base import _normalize_tag_name  # noqa:F401
from ddtrace.contrib.internal.trace_utils_base import _set_url_tag  # noqa:F401
from ddtrace.contrib.internal.trace_utils_base import _store_security_testing_headers  # noqa:F401
from ddtrace.contrib.internal.trace_utils_base import set_user  # noqa:F401
from ddtrace.internal import core
from ddtrace.internal.compat import ensure_text
from ddtrace.internal.constants import _WEB_REQUEST_STARTING_DISPATCHED
from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
from ddtrace.internal.constants import W3C_TRACESTATE_KEY
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._config import config  # noqa:F401
from ddtrace.internal.settings.standalone import standalone_config
from ddtrace.internal.utils.http import w3c_get_tracestate_list_member

# Re-exported for backwards compatibility; the real implementations live in
# ddtrace.internal.utils.service since they have no contrib-specific dependencies.
from ddtrace.internal.utils.service import ext_service  # noqa:F401
from ddtrace.internal.utils.service import int_service  # noqa:F401
from ddtrace.internal.utils.service import set_service_and_source  # noqa:F401
from ddtrace.internal.utils.wrappers import iswrapped  # noqa: F401
from ddtrace.internal.utils.wrappers import unwrap  # noqa: F401


log = get_logger(__name__)

wrap = wrapt.wrap_function_wrapper

# The request-start event is published for WSGI and ASGI applications only: the
# generic WSGI/ASGI middleware, plus Django, whose automatic instrumentation
# bypasses DDWSGIMiddleware. Other web frameworks are out of scope.
#
# The only production listener is registered in MicroVM processes, so both helpers
# check for it before touching the request and normal requests pay one lookup.


def dispatch_wsgi_web_request_starting(environ: MutableMapping[str, Any]) -> None:
    """Publish the request-start event once per WSGI environ (or Django META)."""
    if not core.has_listeners(WebFrameworkEvents.WEB_REQUEST_STARTING.value):
        return
    # Nested layers share environ, e.g. DDWSGIMiddleware wrapping auto-instrumented Django.
    if environ.get(_WEB_REQUEST_STARTING_DISPATCHED):
        return
    environ[_WEB_REQUEST_STARTING_DISPATCHED] = True

    path = (environ.get("SCRIPT_NAME") or "").rstrip("/") + (environ.get("PATH_INFO") or "")
    core.dispatch(WebFrameworkEvents.WEB_REQUEST_STARTING.value, (environ.get("REQUEST_METHOD"), path))


def dispatch_asgi_web_request_starting(scope: Mapping[str, Any]) -> None:
    """Publish the request-start event for a root ASGI HTTP scope."""
    if not core.has_listeners(WebFrameworkEvents.WEB_REQUEST_STARTING.value):
        return

    root_path = (scope.get("root_path") or "").rstrip("/")
    path = scope.get("path") or ""
    # ASGI servers disagree on whether path already includes root_path.
    if root_path and not (path == root_path or path.startswith(root_path + "/")):
        path = root_path + path
    core.dispatch(WebFrameworkEvents.WEB_REQUEST_STARTING.value, (scope.get("method"), path))


def with_traced_module(func):
    """Helper for providing tracing essentials (module and pin) for tracing
    wrappers.

    This helper enables tracing wrappers to dynamically be disabled when the
    corresponding pin is disabled.

    Usage::

        @with_traced_module
        def my_traced_wrapper(django, pin, func, instance, args, kwargs):
            # Do tracing stuff
            pass

        def patch():
            import django
            wrap(django.somefunc, my_traced_wrapper(django))
    """

    def with_mod(mod):
        def wrapper(wrapped, instance, args, kwargs):
            pin = Pin._find(instance, mod)
            if pin and not pin.enabled():
                return wrapped(*args, **kwargs)
            elif not pin:
                log.debug("Pin not found for traced method %r", wrapped)
                return wrapped(*args, **kwargs)
            return func(mod, pin, wrapped, instance, args, kwargs)

        return wrapper

    return with_mod


def is_tracing_enabled() -> bool:
    tracer = core.root.get_item("tracer")
    return tracer is not None and (tracer.enabled or standalone_config.apm_opt_out)


def _copy_trace_level_tags(target_span: Span, parent: Span):
    """
    Copies baggage, tags, origin, sampling decision from parent span to target span.
    """
    for key, value in parent.context._baggage.items():
        target_span.context.set_baggage_item(key, value)
        target_span._set_attribute(f"baggage.{key}", value)

    if parent.context.sampling_priority is not None:
        target_span.context.sampling_priority = parent.context.sampling_priority

    # Materialize a deferred local decision before detaching it from the parent trace.
    _ = parent.context._tracestate
    raw_tracestate = parent.context._meta.get(W3C_TRACESTATE_KEY, "")
    ot_value = w3c_get_tracestate_list_member(raw_tracestate, "ot")
    if ot_value is not None:
        # WebSocket message spans start independent traces. Copy only canonical ot=
        # sampling data; other vendor members describe the parent trace.
        target_span.context._meta[W3C_TRACESTATE_KEY] = "ot=" + ot_value

    if parent.context._meta.get(_ORIGIN_KEY):
        target_span._set_attribute(_ORIGIN_KEY, parent.context._meta[_ORIGIN_KEY])

    if parent.context._meta.get(SAMPLING_DECISION_TRACE_TAG_KEY):
        target_span._set_attribute(
            SAMPLING_DECISION_TRACE_TAG_KEY, parent.context._meta[SAMPLING_DECISION_TRACE_TAG_KEY]
        )


def _flatten(
    obj: Any,
    sep: str = ".",
    prefix: str = "",
    exclude_policy: Optional[Callable[[str], bool]] = None,
) -> Generator[tuple[str, Any], None, None]:
    s = deque()  # type: ignore
    s.append((prefix, obj))
    while s:
        p, v = s.pop()
        if exclude_policy is not None and exclude_policy(p):
            continue
        if isinstance(v, dict):
            s.extend((sep.join((p, k)) if p else k, v) for k, v in v.items())
        else:
            yield p, v


def set_flattened_tags(
    span: Span,
    items: Iterator[tuple[str, Any]],
    sep: str = ".",
    exclude_policy: Optional[Callable[[str], bool]] = None,
    processor: Optional[Callable[[Any], Any]] = None,
) -> None:
    for prefix, value in items:
        for tag, v in _flatten(value, sep, prefix, exclude_policy):
            v = processor(v) if processor is not None else v
            if isinstance(v, bool):
                v = str(v)
            span.set_tag(tag, v)


def extract_netloc_and_query_info_from_url(url: str) -> tuple[str, str]:
    parse_result = parse.urlparse(url)
    query = parse_result.query

    # Relative URLs don't have a netloc, so we force them
    if not parse_result.netloc:
        parse_result = parse.urlparse(f"//{url}")

    netloc = parse_result.netloc.split("@", 1)[-1]  # Discard auth info
    netloc = netloc.split(":", 1)[0]  # Discard port information
    return netloc, query


class InterruptException(Exception):
    pass


def _convert_to_string(attr):
    # ensures attribute is converted to a string
    if attr:
        if isinstance(attr, int) or isinstance(attr, float):
            return str(attr)
        else:
            return ensure_text(attr)
    return attr


def check_module_path(module, attr_path):
    """
    Helper function to safely check if a nested attribute path exists on a module.

    Args:
        module: The root module object
        attr_path: Dot-separated path to the attribute (e.g., "flows.llm_flows.functions")

    Returns:
        bool: True if the full path exists, False otherwise

    Example:
        check_module_path(adk, "flows.llm_flows.functions.__call_tool_async")
        check_module_path(adk, "agents.llm_agent.LlmAgent")
    """
    if not module:
        return False

    try:
        current = module
        for attr in attr_path.split("."):
            if not hasattr(current, attr):
                return False
            current = getattr(current, attr)

        return True
    except (ImportError, AttributeError):
        # Some modules may raise ImportError when accessing attributes that require
        # additional dependencies (e.g., ContainerCodeExecutor requiring Docker for google-adk and an extra pkg install)
        return False
