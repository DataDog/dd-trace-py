from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.trace_utils import set_service_and_source
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_cloud_api_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.trace import tracer
from ddtrace.vendor.packaging.version import parse as parse_version


DD_PATCH_ATTR = "_datadog_patch"

SERVICE_NAME = schematize_service_name("algoliasearch")
APP_NAME = "algoliasearch"
V0 = parse_version("0.0")
V1 = parse_version("1.0")
V2 = parse_version("2.0")
V4 = parse_version("4.0")

try:
    import algoliasearch

    try:
        # algoliasearch < 4 exposes VERSION at algoliasearch.version
        from algoliasearch.version import VERSION
    except ImportError:
        # algoliasearch >= 4 exposes the version at the package root instead
        VERSION = getattr(algoliasearch, "__version__", "0.0")

    algoliasearch_version = parse_version(VERSION)

    # Default configuration
    config._add("algoliasearch", dict(_default_service=SERVICE_NAME, collect_query_text=False))
except ImportError:
    algoliasearch_version = VERSION = V0


def get_version() -> str:
    return VERSION


def _supported_versions() -> dict[str, str]:
    return {"algoliasearch": ">=2.6.3"}


def patch():
    if algoliasearch_version == V0:
        return

    if getattr(algoliasearch, DD_PATCH_ATTR, False):
        return

    algoliasearch._datadog_patch = True

    pin = Pin()

    if algoliasearch_version < V2 and algoliasearch_version >= V1:
        _w(algoliasearch.index, "Index.search", _patched_search)
        pin.onto(algoliasearch.index.Index)
    elif algoliasearch_version >= V2 and algoliasearch_version < V4:
        from algoliasearch import search_index

        _w(algoliasearch, "search_index.SearchIndex.search", _patched_search)
        pin.onto(search_index.SearchIndex)
    elif algoliasearch_version >= V4:
        # algoliasearch 4.x moved the search entry points onto SearchClient(Sync).
        # The old algoliasearch.search_index.SearchIndex.search hook no longer exists.
        from algoliasearch.search.client import SearchClient
        from algoliasearch.search.client import SearchClientSync

        _w("algoliasearch.search.client", "SearchClientSync.search_single_index", _patched_search_v3_single)
        _w("algoliasearch.search.client", "SearchClientSync.search", _patched_search_v3_multi)
        _w("algoliasearch.search.client", "SearchClient.search_single_index", _patched_search_v3_single_async)
        _w("algoliasearch.search.client", "SearchClient.search", _patched_search_v3_multi_async)
        pin.onto(SearchClientSync)
        pin.onto(SearchClient)


def unpatch():
    if algoliasearch_version == V0:
        return

    if getattr(algoliasearch, DD_PATCH_ATTR, False):
        setattr(algoliasearch, DD_PATCH_ATTR, False)

        if algoliasearch_version < V2 and algoliasearch_version >= V1:
            _u(algoliasearch.index.Index, "search")
        elif algoliasearch_version >= V2 and algoliasearch_version < V4:
            from algoliasearch import search_index

            _u(search_index.SearchIndex, "search")
        elif algoliasearch_version >= V4:
            from algoliasearch.search.client import SearchClient
            from algoliasearch.search.client import SearchClientSync

            _u(SearchClientSync, "search_single_index")
            _u(SearchClientSync, "search")
            _u(SearchClient, "search_single_index")
            _u(SearchClient, "search")


# DEV: this maps serves the dual purpose of enumerating the algoliasearch.search() query_args that
# will be sent along as tags, as well as converting arguments names into tag names compliant with
# tag naming recommendations set out here: https://docs.datadoghq.com/tagging/
QUERY_ARGS_DD_TAG_MAP = {
    "page": "page",
    "hitsPerPage": "hits_per_page",
    "attributesToRetrieve": "attributes_to_retrieve",
    "attributesToHighlight": "attributes_to_highlight",
    "attributesToSnippet": "attributes_to_snippet",
    "minWordSizefor1Typo": "min_word_size_for_1_typo",
    "minWordSizefor2Typos": "min_word_size_for_2_typos",
    "getRankingInfo": "get_ranking_info",
    "aroundLatLng": "around_lat_lng",
    "numericFilters": "numeric_filters",
    "tagFilters": "tag_filters",
    "queryType": "query_type",
    "optionalWords": "optional_words",
    "distinct": "distinct",
}


def _patched_search(func, instance, wrapt_args, wrapt_kwargs):
    """
    wrapt_args is called the way it is to distinguish it from the 'args'
    argument to the algoliasearch.index.Index.search() method.
    """

    if algoliasearch_version < V2 and algoliasearch_version >= V1:
        function_query_arg_name = "args"
    elif algoliasearch_version >= V2 and algoliasearch_version < V4:
        function_query_arg_name = "request_options"
    else:
        return func(*wrapt_args, **wrapt_kwargs)

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*wrapt_args, **wrapt_kwargs)

    with tracer.trace(
        schematize_cloud_api_operation("algoliasearch.search", cloud_provider="algoliasearch", cloud_service="search"),
        span_type=SpanTypes.HTTP,
    ) as span:
        set_service_and_source(span, trace_utils.ext_service(pin, config.algoliasearch), config.algoliasearch)
        span._set_attribute(COMPONENT, config.algoliasearch.integration_name)

        # set span.kind to the type of request being performed
        span._set_attribute(SPAN_KIND, SpanKind.CLIENT)

        span._set_attribute(_SPAN_MEASURED_KEY, 1)
        if span.context.sampling_priority is not None and span.context.sampling_priority <= 0:
            return func(*wrapt_args, **wrapt_kwargs)

        if config.algoliasearch.collect_query_text:
            span._set_attribute("query.text", wrapt_kwargs.get("query", wrapt_args[0]))

        query_args = wrapt_kwargs.get(function_query_arg_name, wrapt_args[1] if len(wrapt_args) > 1 else None)

        if query_args and isinstance(query_args, dict):
            for query_arg, tag_name in QUERY_ARGS_DD_TAG_MAP.items():
                value = query_args.get(query_arg)
                if value is not None:
                    span.set_tag("query.args.{}".format(tag_name), value)

        # Result would look like this
        # {
        #   'hits': [
        #     {
        #       .... your search results ...
        #     }
        #   ],
        #   'processingTimeMS': 1,
        #   'nbHits': 1,
        #   'hitsPerPage': 20,
        #   'exhaustiveNbHits': true,
        #   'params': 'query=xxx',
        #   'nbPages': 1,
        #   'query': 'xxx',
        #   'page': 0
        # }
        result = func(*wrapt_args, **wrapt_kwargs)

        if isinstance(result, dict):
            if result.get("processingTimeMS", None) is not None:
                span._set_attribute("processing_time_ms", int(result["processingTimeMS"]))

            if result.get("nbHits", None) is not None:
                span._set_attribute("number_of_hits", int(result["nbHits"]))

        return result


def _extract_v3_query_and_args(func_name, wrapt_args, wrapt_kwargs):
    """Return ``(query_text, params_dict)`` for the modern algoliasearch client.

    - ``search_single_index(index_name, search_params=..., request_options=...)``:
      the query text lives inside ``search_params['query']`` (or the equivalent
      attribute on a ``SearchParams`` model). ``index_name`` is NOT the query.
    - ``search(search_method_params, request_options=...)`` is a multi-query API.
      We best-effort extract the first request's query/args so users get some
      telemetry, but skip when the payload shape doesn't line up.
    """
    query_text = None
    params_dict = None

    if func_name == "search_single_index":
        search_params = wrapt_kwargs.get("search_params")
        if search_params is None and len(wrapt_args) > 1:
            search_params = wrapt_args[1]
        params_dict = _to_plain_dict(search_params)
        if isinstance(params_dict, dict):
            query_text = params_dict.get("query")
    elif func_name == "search":
        search_method_params = wrapt_kwargs.get("search_method_params")
        if search_method_params is None and len(wrapt_args) > 0:
            search_method_params = wrapt_args[0]
        method_dict = _to_plain_dict(search_method_params)
        if isinstance(method_dict, dict):
            requests = method_dict.get("requests") or []
            if requests:
                first = _to_plain_dict(requests[0])
                if isinstance(first, dict):
                    params_dict = first
                    query_text = first.get("query")

    return query_text, params_dict


def _to_plain_dict(obj):
    """Normalize a pydantic model / dict / other into a plain dict when possible."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            result = to_dict()
            if isinstance(result, dict):
                return result
        except Exception:
            return None
    return None


def _configure_v3_span(span, pin):
    set_service_and_source(span, trace_utils.ext_service(pin, config.algoliasearch), config.algoliasearch)
    span._set_attribute(COMPONENT, config.algoliasearch.integration_name)
    span._set_attribute(SPAN_KIND, SpanKind.CLIENT)
    span._set_attribute(_SPAN_MEASURED_KEY, 1)


def _tag_v3_request(span, query_text, params_dict):
    if config.algoliasearch.collect_query_text and query_text is not None:
        span._set_attribute("query.text", query_text)

    if params_dict and isinstance(params_dict, dict):
        for query_arg, tag_name in QUERY_ARGS_DD_TAG_MAP.items():
            value = params_dict.get(query_arg)
            if value is not None:
                span.set_tag("query.args.{}".format(tag_name), value)


def _tag_v3_result(span, result):
    # The v3/v4 client returns a pydantic model (SearchResponse / SearchResponses)
    # whose ``.to_dict()`` re-serialises to the historical camelCase JSON keys
    # (``processingTimeMS``, ``nbHits``). Legacy dict returns still work.
    result_dict = None
    if isinstance(result, dict):
        result_dict = result
    else:
        result_dict = _to_plain_dict(result)

    if isinstance(result_dict, dict):
        if "results" in result_dict:
            results = result_dict.get("results") or []
            if isinstance(results, list) and results:
                first_result = _to_plain_dict(results[0])
                if isinstance(first_result, dict):
                    result_dict = first_result

        processing_time = result_dict.get("processingTimeMS")
        if processing_time is None:
            processing_time = result_dict.get("processing_time_ms")
        if processing_time is not None:
            try:
                span._set_attribute("processing_time_ms", int(processing_time))
            except (TypeError, ValueError):
                pass

        nb_hits = result_dict.get("nbHits")
        if nb_hits is None:
            nb_hits = result_dict.get("nb_hits")
        if nb_hits is not None:
            try:
                span._set_attribute("number_of_hits", int(nb_hits))
            except (TypeError, ValueError):
                pass


def _patched_search_v3_single(func, instance, wrapt_args, wrapt_kwargs):
    return _patched_search_v3(func, instance, wrapt_args, wrapt_kwargs, "search_single_index")


def _patched_search_v3_multi(func, instance, wrapt_args, wrapt_kwargs):
    return _patched_search_v3(func, instance, wrapt_args, wrapt_kwargs, "search")


async def _patched_search_v3_single_async(func, instance, wrapt_args, wrapt_kwargs):
    return await _patched_search_v3_async(func, instance, wrapt_args, wrapt_kwargs, "search_single_index")


async def _patched_search_v3_multi_async(func, instance, wrapt_args, wrapt_kwargs):
    return await _patched_search_v3_async(func, instance, wrapt_args, wrapt_kwargs, "search")


def _patched_search_v3(func, instance, wrapt_args, wrapt_kwargs, func_name):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*wrapt_args, **wrapt_kwargs)

    with tracer.trace(
        schematize_cloud_api_operation("algoliasearch.search", cloud_provider="algoliasearch", cloud_service="search"),
        span_type=SpanTypes.HTTP,
    ) as span:
        _configure_v3_span(span, pin)
        if span.context.sampling_priority is not None and span.context.sampling_priority <= 0:
            return func(*wrapt_args, **wrapt_kwargs)

        query_text, params_dict = _extract_v3_query_and_args(func_name, wrapt_args, wrapt_kwargs)
        _tag_v3_request(span, query_text, params_dict)

        result = func(*wrapt_args, **wrapt_kwargs)
        _tag_v3_result(span, result)
        return result


async def _patched_search_v3_async(func, instance, wrapt_args, wrapt_kwargs, func_name):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*wrapt_args, **wrapt_kwargs)

    with tracer.trace(
        schematize_cloud_api_operation("algoliasearch.search", cloud_provider="algoliasearch", cloud_service="search"),
        span_type=SpanTypes.HTTP,
    ) as span:
        _configure_v3_span(span, pin)
        if span.context.sampling_priority is not None and span.context.sampling_priority <= 0:
            return await func(*wrapt_args, **wrapt_kwargs)

        query_text, params_dict = _extract_v3_query_and_args(func_name, wrapt_args, wrapt_kwargs)
        _tag_v3_request(span, query_text, params_dict)

        result = await func(*wrapt_args, **wrapt_kwargs)
        _tag_v3_result(span, result)
        return result
