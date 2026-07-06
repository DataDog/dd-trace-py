from typing import Any
from typing import NamedTuple
from typing import Optional
from typing import cast

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.trace_utils import set_service_and_source
from ddtrace.ext import SpanKind
from ddtrace.internal.constants import COMPONENT
from ddtrace.trace import Span


_MISSING = object()


class SearchRequest(NamedTuple):
    query_text: Optional[Any]
    params: Optional[dict[str, Any]]


# DEV: this map serves the dual purpose of enumerating the algoliasearch.search() query_args that
# will be sent along as tags, as well as converting argument names into tag names compliant with
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


def configure_search_span(span: Span, pin: Pin) -> None:
    service = cast(str, trace_utils.ext_service(pin, config.algoliasearch))
    set_service_and_source(span, service, config.algoliasearch)
    span._set_attribute(COMPONENT, config.algoliasearch.integration_name)
    span._set_attribute(SPAN_KIND, SpanKind.CLIENT)
    span._set_attribute(_SPAN_MEASURED_KEY, 1)


def extract_legacy_search_request(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    query_args_name: str,
) -> SearchRequest:
    query_text = _get_argument(args, kwargs, 0, "query")
    params = _to_plain_dict(_get_argument(args, kwargs, 1, query_args_name))
    return SearchRequest(query_text=query_text, params=params)


def extract_search_single_index_request(args: tuple[Any, ...], kwargs: dict[str, Any]) -> SearchRequest:
    search_params = _get_argument(args, kwargs, 1, "search_params")
    params = _to_plain_dict(search_params)
    query_text = params.get("query") if params else None
    return SearchRequest(query_text=query_text, params=params)


def extract_search_request(args: tuple[Any, ...], kwargs: dict[str, Any]) -> SearchRequest:
    search_method_params = _get_argument(args, kwargs, 0, "search_method_params")
    method_params = _to_plain_dict(search_method_params)
    if not method_params:
        return SearchRequest(query_text=None, params=None)

    requests = method_params.get("requests")
    if not isinstance(requests, (list, tuple)) or not requests:
        return SearchRequest(query_text=None, params=None)

    params = _to_plain_dict(requests[0])
    query_text = params.get("query") if params else None
    return SearchRequest(query_text=query_text, params=params)


def tag_search_request(span: Span, request: SearchRequest) -> None:
    if config.algoliasearch.collect_query_text and request.query_text is not None:
        span._set_attribute("query.text", request.query_text)

    if not request.params:
        return

    for query_arg, tag_name in QUERY_ARGS_DD_TAG_MAP.items():
        value = request.params.get(query_arg)
        if value is not None:
            span.set_tag("query.args.{}".format(tag_name), value)


def tag_search_result(span: Span, result: Any) -> None:
    result_dict = _extract_search_result(result)
    if result_dict is None:
        return

    _set_int_attribute(
        span, "processing_time_ms", _first_present(result_dict, "processingTimeMS", "processing_time_ms")
    )
    _set_int_attribute(span, "number_of_hits", _first_present(result_dict, "nbHits", "nb_hits"))


def _get_argument(args: tuple[Any, ...], kwargs: dict[str, Any], position: int, name: str) -> Any:
    value = kwargs.get(name, _MISSING)
    if value is not _MISSING:
        return value
    if len(args) > position:
        return args[position]
    return None


def _to_plain_dict(obj: Any) -> Optional[dict[str, Any]]:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj

    for method_name in ("to_dict", "model_dump", "dict"):
        to_dict = getattr(obj, method_name, None)
        if callable(to_dict):
            try:
                result = to_dict()
            except (AttributeError, TypeError, ValueError):
                continue
            if isinstance(result, dict):
                return result
    return None


def _extract_search_result(result: Any) -> Optional[dict[str, Any]]:
    result_dict = _to_plain_dict(result)
    if result_dict is None:
        return None

    results = result_dict.get("results")
    if isinstance(results, list) and results:
        first_result = _to_plain_dict(results[0])
        if first_result is not None:
            return first_result

    return result_dict


def _set_int_attribute(span: Span, name: str, value: Any) -> None:
    if value is None:
        return

    try:
        span._set_attribute(name, int(value))
    except (TypeError, ValueError):
        pass


def _first_present(values: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in values:
            return values[key]
    return None
