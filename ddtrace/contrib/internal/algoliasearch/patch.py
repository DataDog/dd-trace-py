from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.contrib.internal.trace_utils import set_service_and_source
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_cloud_api_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.trace import tracer
from ddtrace.vendor.packaging.version import parse as parse_version


DD_PATCH_ATTR = "_datadog_patch"

SERVICE_NAME = schematize_service_name("algoliasearch")
APP_NAME = "algoliasearch"
V0 = parse_version("0.0")
V1 = parse_version("1.0")
V2 = parse_version("2.0")
# 4.0 prereleases sort below 4.0 and already ship the rewritten client, so the
# cutoff has to sit below them rather than at 4.0 itself.
V4 = parse_version("4.0.dev0")

try:
    import algoliasearch

    try:
        from algoliasearch.version import VERSION
    except ImportError:
        # algoliasearch >= 4 is a generated client and replaced the version
        # submodule with a package level __version__.
        VERSION = getattr(algoliasearch, "__version__", "")

    algoliasearch_version = parse_version(VERSION) if VERSION else V0

    # Default configuration
    config._add("algoliasearch", dict(_default_service=SERVICE_NAME, collect_query_text=False))
except ImportError:
    algoliasearch_version = V0
    VERSION = ""


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

    if algoliasearch_version < V2 and algoliasearch_version >= V1:
        _w(algoliasearch.index, "Index.search", _patched_search)
    elif algoliasearch_version >= V2 and algoliasearch_version < V4:
        from algoliasearch import search_index

        _w(search_index, "SearchIndex.search", _patched_search)
    elif algoliasearch_version >= V4:
        from algoliasearch.search import client

        _w(client, "SearchClientSync.search_single_index", _patched_search)
        _w(client, "SearchClient.search_single_index", _patched_search_async)


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
            from algoliasearch.search import client

            _u(client.SearchClientSync, "search_single_index")
            _u(client.SearchClient, "search_single_index")


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


def _start_span():
    span = tracer.trace(
        schematize_cloud_api_operation("algoliasearch.search", cloud_provider="algoliasearch", cloud_service="search"),
        span_type=SpanTypes.HTTP,
    )
    set_service_and_source(span, trace_utils.ext_service(None, config.algoliasearch), config.algoliasearch)
    span._set_attribute(COMPONENT, config.algoliasearch.integration_name)

    # set span.kind to the type of request being performed
    span._set_attribute(SPAN_KIND, SpanKind.CLIENT)

    span._set_attribute(_SPAN_MEASURED_KEY, 1)
    return span


def _set_query_tags(span, query_text, query_args):
    if config.algoliasearch.collect_query_text and query_text is not None:
        span._set_attribute("query.text", query_text)

    if query_args and isinstance(query_args, dict):
        for query_arg, tag_name in QUERY_ARGS_DD_TAG_MAP.items():
            value = query_args.get(query_arg)
            if value is not None:
                span.set_tag(f"query.args.{tag_name}", value)


def _set_result_tags(span, processing_time_ms, number_of_hits):
    if processing_time_ms is not None:
        span._set_attribute("processing_time_ms", int(processing_time_ms))

    if number_of_hits is not None:
        span._set_attribute("number_of_hits", int(number_of_hits))


def _patched_search(func, instance, wrapt_args, wrapt_kwargs):
    """
    wrapt_args is called the way it is to distinguish it from the 'args'
    argument to the algoliasearch.index.Index.search() method.
    """

    if algoliasearch_version < V2 and algoliasearch_version >= V1:
        function_query_arg_name = "args"
    elif algoliasearch_version >= V2 and algoliasearch_version < V4:
        function_query_arg_name = "request_options"
    elif algoliasearch_version >= V4:
        return _patched_search_single_index(func, wrapt_args, wrapt_kwargs)
    else:
        return func(*wrapt_args, **wrapt_kwargs)

    if not is_tracing_enabled():
        return func(*wrapt_args, **wrapt_kwargs)

    with _start_span() as span:
        if span.context.sampling_priority is not None and span.context.sampling_priority <= 0:
            return func(*wrapt_args, **wrapt_kwargs)

        query_text = get_argument_value(wrapt_args, wrapt_kwargs, 0, "query", optional=True)
        query_args = get_argument_value(wrapt_args, wrapt_kwargs, 1, function_query_arg_name, optional=True)
        _set_query_tags(span, query_text, query_args)

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
            _set_result_tags(span, result.get("processingTimeMS"), result.get("nbHits"))

        return result


# algoliasearch >= 4 is a generated client: ``search_single_index(index_name, search_params, request_options)``
# takes the query inside ``search_params`` (a dict or a model) and returns a ``SearchResponse`` model.
def _v4_search_params(wrapt_args, wrapt_kwargs) -> dict:
    search_params = get_argument_value(wrapt_args, wrapt_kwargs, 1, "search_params", optional=True)
    if hasattr(search_params, "to_dict"):
        # Models serialize back to the camelCase keys the dict form and QUERY_ARGS_DD_TAG_MAP use.
        search_params = search_params.to_dict()
    return search_params if isinstance(search_params, dict) else {}


def _set_v4_request_tags(span, wrapt_args, wrapt_kwargs):
    query_args = _v4_search_params(wrapt_args, wrapt_kwargs)
    _set_query_tags(span, query_args.get("query"), query_args)


def _set_v4_result_tags(span, result):
    _set_result_tags(span, getattr(result, "processing_time_ms", None), getattr(result, "nb_hits", None))


def _patched_search_single_index(func, wrapt_args, wrapt_kwargs):
    if not is_tracing_enabled():
        return func(*wrapt_args, **wrapt_kwargs)

    with _start_span() as span:
        if span.context.sampling_priority is not None and span.context.sampling_priority <= 0:
            return func(*wrapt_args, **wrapt_kwargs)

        _set_v4_request_tags(span, wrapt_args, wrapt_kwargs)
        result = func(*wrapt_args, **wrapt_kwargs)
        _set_v4_result_tags(span, result)
        return result


async def _patched_search_async(func, instance, wrapt_args, wrapt_kwargs):
    if not is_tracing_enabled():
        return await func(*wrapt_args, **wrapt_kwargs)

    with _start_span() as span:
        if span.context.sampling_priority is not None and span.context.sampling_priority <= 0:
            return await func(*wrapt_args, **wrapt_kwargs)

        _set_v4_request_tags(span, wrapt_args, wrapt_kwargs)
        result = await func(*wrapt_args, **wrapt_kwargs)
        _set_v4_result_tags(span, result)
        return result
