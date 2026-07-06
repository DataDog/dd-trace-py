from typing import Any
from typing import Awaitable
from typing import Callable

from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.ext import SpanTypes
from ddtrace.internal.schema import schematize_cloud_api_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.trace import tracer
from ddtrace.vendor.packaging.version import parse as parse_version

from .utils import SearchRequest
from .utils import configure_search_span
from .utils import extract_legacy_search_request
from .utils import extract_search_request
from .utils import extract_search_single_index_request
from .utils import tag_search_request
from .utils import tag_search_result


DD_PATCH_ATTR = "_datadog_patch"

SERVICE_NAME = schematize_service_name("algoliasearch")
V0 = parse_version("0.0")
V1 = parse_version("1.0")
V2 = parse_version("2.0")
V4 = parse_version("4.0")

try:
    import algoliasearch

    try:
        from algoliasearch.version import VERSION
    except ImportError:
        VERSION = getattr(algoliasearch, "__version__", "0.0")

    algoliasearch_version = parse_version(VERSION)

    config._add("algoliasearch", dict(_default_service=SERVICE_NAME, collect_query_text=False))
except ImportError:
    VERSION = "0.0"
    algoliasearch_version = V0


def get_version() -> str:
    return VERSION


def _supported_versions() -> dict[str, str]:
    return {"algoliasearch": ">=2.6.3"}


def patch() -> None:
    if algoliasearch_version == V0:
        return

    if getattr(algoliasearch, DD_PATCH_ATTR, False):
        return

    algoliasearch._datadog_patch = True

    pin = Pin()
    if _uses_v1_index_client():
        _w(algoliasearch.index.Index, "search", _traced_legacy_search)
        pin.onto(algoliasearch.index.Index)
    elif _uses_search_index_client():
        from algoliasearch import search_index

        _w(search_index.SearchIndex, "search", _traced_legacy_search)
        pin.onto(search_index.SearchIndex)
    elif _uses_modern_search_client():
        _patch_modern_search_client(pin)


def unpatch() -> None:
    if algoliasearch_version == V0:
        return

    if not getattr(algoliasearch, DD_PATCH_ATTR, False):
        return

    setattr(algoliasearch, DD_PATCH_ATTR, False)
    if _uses_v1_index_client():
        _u(algoliasearch.index.Index, "search")
    elif _uses_search_index_client():
        from algoliasearch import search_index

        _u(search_index.SearchIndex, "search")
    elif _uses_modern_search_client():
        _unpatch_modern_search_client()


def _uses_v1_index_client() -> bool:
    return V1 <= algoliasearch_version < V2


def _uses_search_index_client() -> bool:
    return V2 <= algoliasearch_version < V4


def _uses_modern_search_client() -> bool:
    return algoliasearch_version >= V4


def _patch_modern_search_client(pin: Pin) -> None:
    from algoliasearch.search.client import SearchClient
    from algoliasearch.search.client import SearchClientSync

    _w(SearchClientSync, "search_single_index", _traced_sync_search_single_index)
    _w(SearchClientSync, "search", _traced_sync_search)
    _w(SearchClient, "search_single_index", _traced_async_search_single_index)
    _w(SearchClient, "search", _traced_async_search)
    pin.onto(SearchClientSync)
    pin.onto(SearchClient)


def _unpatch_modern_search_client() -> None:
    from algoliasearch.search.client import SearchClient
    from algoliasearch.search.client import SearchClientSync

    _u(SearchClientSync, "search_single_index")
    _u(SearchClientSync, "search")
    _u(SearchClient, "search_single_index")
    _u(SearchClient, "search")


def _traced_legacy_search(
    wrapped: Callable[..., Any],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    query_args_name = "args" if _uses_v1_index_client() else "request_options"
    request = extract_legacy_search_request(args, kwargs, query_args_name)
    return _trace_search(wrapped, instance, args, kwargs, request)


def _traced_sync_search_single_index(
    wrapped: Callable[..., Any],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    request = extract_search_single_index_request(args, kwargs)
    return _trace_search(wrapped, instance, args, kwargs, request)


def _traced_sync_search(
    wrapped: Callable[..., Any],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    request = extract_search_request(args, kwargs)
    return _trace_search(wrapped, instance, args, kwargs, request)


async def _traced_async_search_single_index(
    wrapped: Callable[..., Awaitable[Any]],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    request = extract_search_single_index_request(args, kwargs)
    return await _trace_search_async(wrapped, instance, args, kwargs, request)


async def _traced_async_search(
    wrapped: Callable[..., Awaitable[Any]],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    request = extract_search_request(args, kwargs)
    return await _trace_search_async(wrapped, instance, args, kwargs, request)


def _trace_search(
    wrapped: Callable[..., Any],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    request: SearchRequest,
) -> Any:
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    with tracer.trace(_operation_name(), span_type=SpanTypes.HTTP) as span:
        configure_search_span(span, pin)
        if span.context.sampling_priority is not None and span.context.sampling_priority <= 0:
            return wrapped(*args, **kwargs)

        tag_search_request(span, request)
        result = wrapped(*args, **kwargs)
        tag_search_result(span, result)
        return result


async def _trace_search_async(
    wrapped: Callable[..., Awaitable[Any]],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    request: SearchRequest,
) -> Any:
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await wrapped(*args, **kwargs)

    with tracer.trace(_operation_name(), span_type=SpanTypes.HTTP) as span:
        configure_search_span(span, pin)
        if span.context.sampling_priority is not None and span.context.sampling_priority <= 0:
            return await wrapped(*args, **kwargs)

        tag_search_request(span, request)
        result = await wrapped(*args, **kwargs)
        tag_search_result(span, result)
        return result


def _operation_name() -> str:
    return schematize_cloud_api_operation(
        "algoliasearch.search", cloud_provider="algoliasearch", cloud_service="search"
    )
