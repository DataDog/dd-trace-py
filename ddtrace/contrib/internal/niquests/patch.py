import os
from typing import Any
from typing import Optional
from urllib import parse

import niquests
from wrapt import BoundFunctionWrapper
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import USER_AGENT_HEADER
from ddtrace.internal.logger import get_logger
from ddtrace.internal.opentelemetry.constants import OTLP_EXPORTER_HEADER_IDENTIFIER
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u


log = get_logger(__name__)

NIQUESTS_REQUEST_TAGS = {COMPONENT: config.niquests.integration_name, SPAN_KIND: SpanKind.CLIENT}


def get_version() -> str:
    return getattr(niquests, "__version__", "")


config._add(
    "niquests",
    {
        "distributed_tracing": asbool(os.getenv("DD_NIQUESTS_DISTRIBUTED_TRACING", default=True)),
        "split_by_domain": asbool(os.getenv("DD_NIQUESTS_SPLIT_BY_DOMAIN", default=False)),
        "default_http_tag_query_string": config._http_client_tag_query_string,
        "_default_service": schematize_service_name("niquests"),
    },
)


def _supported_versions() -> dict[str, str]:
    return {"niquests": ">=3.17.0"}


def is_otlp_export(request: niquests.models.Request) -> bool:
    if not (config._otel_logs_enabled or config._otel_metrics_enabled):
        return False
    user_agent = request.headers.get(USER_AGENT_HEADER, "")
    normalized_user_agent = user_agent.lower().replace(" ", "-")
    if OTLP_EXPORTER_HEADER_IDENTIFIER in normalized_user_agent:
        return True
    return False


def _extract_hostname_and_path(uri):
    parsed_uri = parse.urlparse(uri)
    hostname = parsed_uri.hostname
    try:
        if parsed_uri.port is not None:
            hostname = "%s:%s" % (hostname, str(parsed_uri.port))
    except ValueError:
        hostname = "%s:?" % (hostname,)
    return hostname, parsed_uri.path


def _extract_query_string(uri):
    start = uri.find("?") + 1
    if start == 0:
        return None

    end = len(uri)
    j = uri.rfind("#", 0, end)
    if j != -1:
        end = j

    if end <= start:
        return None

    return uri[start:end]


def _get_service_name(request) -> Optional[str]:
    if config.niquests.split_by_domain:
        from ddtrace.contrib.internal.trace_utils import _sanitized_url

        url = _sanitized_url(request.url)
        hostname, _ = _extract_hostname_and_path(url)
        if hostname:
            return hostname
    return ext_service(None, config.niquests)


def _wrap_send_sync(
    wrapped: BoundFunctionWrapper,
    instance: niquests.Session,
    args: tuple[niquests.PreparedRequest],
    kwargs: dict[str, Any],
) -> niquests.Response:
    req = get_argument_value(args, kwargs, 0, "request")
    if not req or is_otlp_export(req):
        return wrapped(*args, **kwargs)

    with core.context_with_data(
        "niquests.request",
        call_trace=True,
        span_name=schematize_url_operation("niquests.request", protocol="http", direction=SpanDirection.OUTBOUND),
        span_type=SpanTypes.HTTP,
        service=_get_service_name(req),
        tags=NIQUESTS_REQUEST_TAGS,
        request=req,
    ) as ctx:
        resp = None
        try:
            resp = wrapped(*args, **kwargs)
            return resp
        finally:
            ctx.set_item("response", resp)


async def _wrap_send_async(
    wrapped: BoundFunctionWrapper,
    instance: niquests.AsyncSession,
    args: tuple[niquests.PreparedRequest],
    kwargs: dict[str, Any],
):
    req = get_argument_value(args, kwargs, 0, "request")
    if not req or is_otlp_export(req):
        return await wrapped(*args, **kwargs)

    with core.context_with_data(
        "niquests.request",
        call_trace=True,
        span_name=schematize_url_operation("niquests.request", protocol="http", direction=SpanDirection.OUTBOUND),
        span_type=SpanTypes.HTTP,
        service=_get_service_name(req),
        tags=NIQUESTS_REQUEST_TAGS,
        request=req,
    ) as ctx:
        resp = None
        try:
            resp = await wrapped(*args, **kwargs)
            return resp
        finally:
            ctx.set_item("response", resp)


def _wrap_gather_sync(
    wrapped: BoundFunctionWrapper,
    instance: niquests.Session,
    args: tuple,
    kwargs: dict[str, Any],
) -> None:
    """Wrap Session.gather() to trace multiplexed response gathering."""
    # gather() is a no-op if multiplexing is disabled
    if not getattr(instance, "multiplexed", False):
        return wrapped(*args, **kwargs)

    # Count responses and max_fetch for resource name
    responses = args if args else []
    max_fetch = kwargs.get("max_fetch", None)

    # Build resource name
    if responses:
        resource = f"gather {len(responses)} responses"
    elif max_fetch:
        resource = f"gather (max {max_fetch})"
    else:
        resource = "gather all"

    with core.context_with_data(
        "niquests.gather",
        call_trace=True,
        span_name="niquests.gather",
        span_type=SpanTypes.HTTP,
        service=config.niquests._default_service,
        tags={COMPONENT: config.niquests.integration_name, SPAN_KIND: SpanKind.CLIENT},
        resource=resource,
        multiplexed=True,
        response_count=len(responses) if responses else None,
        max_fetch=max_fetch,
    ):
        return wrapped(*args, **kwargs)


async def _wrap_gather_async(
    wrapped: BoundFunctionWrapper,
    instance: niquests.AsyncSession,
    args: tuple,
    kwargs: dict[str, Any],
) -> None:
    """Wrap AsyncSession.gather() to trace multiplexed response gathering."""
    # gather() is a no-op if multiplexing is disabled
    if not getattr(instance, "multiplexed", False):
        return await wrapped(*args, **kwargs)

    # Count responses and max_fetch for resource name
    responses = args if args else []
    max_fetch = kwargs.get("max_fetch", None)

    # Build resource name
    if responses:
        resource = f"gather {len(responses)} responses"
    elif max_fetch:
        resource = f"gather (max {max_fetch})"
    else:
        resource = "gather all"

    with core.context_with_data(
        "niquests.gather",
        call_trace=True,
        span_name="niquests.gather",
        span_type=SpanTypes.HTTP,
        service=config.niquests._default_service,
        tags={COMPONENT: config.niquests.integration_name, SPAN_KIND: SpanKind.CLIENT},
        resource=resource,
        multiplexed=True,
        response_count=len(responses) if responses else None,
        max_fetch=max_fetch,
    ):
        return await wrapped(*args, **kwargs)


def patch() -> None:
    if getattr(niquests, "_datadog_patch", False):
        return

    niquests._datadog_patch = True

    _w(niquests.Session, "send", _wrap_send_sync)
    _w(niquests.AsyncSession, "send", _wrap_send_async)
    _w(niquests.Session, "gather", _wrap_gather_sync)
    _w(niquests.AsyncSession, "gather", _wrap_gather_async)


def unpatch() -> None:
    if not getattr(niquests, "_datadog_patch", False):
        return

    niquests._datadog_patch = False

    _u(niquests.Session, "send")
    _u(niquests.AsyncSession, "send")
    _u(niquests.Session, "gather")
    _u(niquests.AsyncSession, "gather")
