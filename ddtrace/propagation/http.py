from typing import Dict
from typing import FrozenSet
from typing import Optional
from typing import Union
from typing import cast

from ..context import Context
from ..internal._tagset import TagsetDecodeError
from ..internal._tagset import TagsetEncodeError
from ..internal._tagset import TagsetMaxSizeError
from ..internal._tagset import decode_tagset_string
from ..internal._tagset import encode_tagset_values
from ..internal.compat import ensure_str
from ..internal.logger import get_logger
from ._utils import get_wsgi_header


log = get_logger(__name__)

# HTTP headers one should set for distributed tracing.
# These are cross-language (eg: Python, Go and other implementations should honor these)
HTTP_HEADER_TRACE_ID = "x-datadog-trace-id"
HTTP_HEADER_PARENT_ID = "x-datadog-parent-id"
HTTP_HEADER_SAMPLING_PRIORITY = "x-datadog-sampling-priority"
HTTP_HEADER_ORIGIN = "x-datadog-origin"
HTTP_HEADER_TAGS = "x-datadog-tags"


# Note that due to WSGI spec we have to also check for uppercased and prefixed
# versions of these headers
POSSIBLE_HTTP_HEADER_TRACE_IDS = frozenset([HTTP_HEADER_TRACE_ID, get_wsgi_header(HTTP_HEADER_TRACE_ID).lower()])
POSSIBLE_HTTP_HEADER_PARENT_IDS = frozenset([HTTP_HEADER_PARENT_ID, get_wsgi_header(HTTP_HEADER_PARENT_ID).lower()])
POSSIBLE_HTTP_HEADER_SAMPLING_PRIORITIES = frozenset(
    [HTTP_HEADER_SAMPLING_PRIORITY, get_wsgi_header(HTTP_HEADER_SAMPLING_PRIORITY).lower()]
)
POSSIBLE_HTTP_HEADER_ORIGIN = frozenset([HTTP_HEADER_ORIGIN, get_wsgi_header(HTTP_HEADER_ORIGIN).lower()])
POSSIBLE_HTTP_HEADER_TAGS = frozenset([HTTP_HEADER_TAGS, get_wsgi_header(HTTP_HEADER_TAGS).lower()])


class HTTPPropagator(object):
    """A HTTP Propagator using HTTP headers as carrier."""

    @staticmethod
    def inject(span_context, headers):
        # type: (Context, Dict[str, str]) -> None
        """Inject Context attributes that have to be propagated as HTTP headers.

        Here is an example using `requests`::

            import requests
            from ddtrace.propagation.http import HTTPPropagator

            def parent_call():
                with tracer.trace('parent_span') as span:
                    headers = {}
                    HTTPPropagator.inject(span.context, headers)
                    url = '<some RPC endpoint>'
                    r = requests.get(url, headers=headers)

        :param Context span_context: Span context to propagate.
        :param dict headers: HTTP headers to extend with tracing attributes.
        """
        headers[HTTP_HEADER_TRACE_ID] = str(span_context.trace_id)
        headers[HTTP_HEADER_PARENT_ID] = str(span_context.span_id)
        sampling_priority = span_context.sampling_priority
        # Propagate priority only if defined
        if sampling_priority is not None:
            headers[HTTP_HEADER_SAMPLING_PRIORITY] = str(span_context.sampling_priority)
        # Propagate origin only if defined
        if span_context.dd_origin is not None:
            headers[HTTP_HEADER_ORIGIN] = str(span_context.dd_origin)

        # Do not try to encode tags if we have already tried and received an error
        if "_dd.propagation_error" in span_context._meta:
            return

        # Only propagate tags that start with `_dd.p.`
        tags_to_encode = {}  # type: Dict[str, str]
        for key, value in span_context._meta.items():
            # DEV: encoding will fail if the key or value are not `str`
            key = ensure_str(key)
            if key.startswith("_dd.p."):
                tags_to_encode[key] = ensure_str(value)

        if tags_to_encode:
            encoded_tags = None

            try:
                encoded_tags = encode_tagset_values(tags_to_encode)
            except TagsetMaxSizeError:
                # We hit the max size allowed, add a tag to the context to indicate this happened
                span_context._meta["_dd.propagation_error"] = "max_size"
                log.warning("failed to encode x-datadog-tags", exc_info=True)
            except TagsetEncodeError:
                # We hit an encoding error, add a tag to the context to indicate this happened
                span_context._meta["_dd.propagation_error"] = "encoding_error"
                log.warning("failed to encode x-datadog-tags", exc_info=True)
            if encoded_tags:
                headers[HTTP_HEADER_TAGS] = encoded_tags

    @staticmethod
    def _extract_header_value(possible_header_names, headers, default=None):
        # type: (FrozenSet[str], Dict[str, str], Optional[str]) -> Optional[str]
        for header in possible_header_names:
            try:
                return headers[header]
            except KeyError:
                pass

        return default

    @staticmethod
    def extract(headers):
        # type: (Dict[str,str]) -> Context
        """Extract a Context from HTTP headers into a new Context.

        Here is an example from a web endpoint::

            from ddtrace.propagation.http import HTTPPropagator

            def my_controller(url, headers):
                context = HTTPPropagator.extract(headers)
                if context:
                    tracer.context_provider.activate(context)

                with tracer.trace('my_controller') as span:
                    span.set_meta('http.url', url)

        :param dict headers: HTTP headers to extract tracing attributes.
        :return: New `Context` with propagated attributes.
        """
        if not headers:
            return Context()

        try:
            normalized_headers = {name.lower(): v for name, v in headers.items()}
            # TODO: Fix variable type changing (mypy)
            trace_id = HTTPPropagator._extract_header_value(
                POSSIBLE_HTTP_HEADER_TRACE_IDS,
                normalized_headers,
            )
            if trace_id is None:
                return Context()

            parent_span_id = HTTPPropagator._extract_header_value(
                POSSIBLE_HTTP_HEADER_PARENT_IDS,
                normalized_headers,
                default="0",
            )
            sampling_priority = HTTPPropagator._extract_header_value(
                POSSIBLE_HTTP_HEADER_SAMPLING_PRIORITIES,
                normalized_headers,
            )
            origin = HTTPPropagator._extract_header_value(
                POSSIBLE_HTTP_HEADER_ORIGIN,
                normalized_headers,
            )
            meta = None
            tags_value = HTTPPropagator._extract_header_value(
                POSSIBLE_HTTP_HEADER_TAGS,
                normalized_headers,
                default="",
            )
            if tags_value:
                # Do not fail if the tags are malformed
                try:
                    # We get a Dict[str, str], but need it to be Dict[Union[str, bytes], str] (e.g. _MetaDictType)
                    meta = cast(Dict[Union[str, bytes], str], decode_tagset_string(tags_value))
                except TagsetDecodeError:
                    log.debug("failed to decode x-datadog-tags: %r", tags_value, exc_info=True)

            # Try to parse values into their expected types
            try:
                if sampling_priority is not None:
                    sampling_priority = int(sampling_priority)  # type: ignore[assignment]
                else:
                    sampling_priority = sampling_priority

                return Context(
                    # DEV: Do not allow `0` for trace id or span id, use None instead
                    trace_id=int(trace_id) or None,
                    span_id=int(parent_span_id) or None,  # type: ignore[arg-type]
                    sampling_priority=sampling_priority,  # type: ignore[arg-type]
                    dd_origin=origin,
                    meta=meta,
                )
            # If headers are invalid and cannot be parsed, return a new context and log the issue.
            except (TypeError, ValueError):
                log.debug(
                    (
                        "received invalid x-datadog-* headers, "
                        "trace-id: %r, parent-id: %r, priority: %r, origin: %r, tags: %r"
                    ),
                    trace_id,
                    parent_span_id,
                    sampling_priority,
                    origin,
                    tags_value,
                )
                return Context()
        except Exception:
            log.debug("error while extracting x-datadog-* headers", exc_info=True)
            return Context()
