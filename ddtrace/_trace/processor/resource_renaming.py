import re
from typing import Optional
from typing import Union
from urllib.parse import urlparse

from ddtrace._trace.processor import SpanProcessor
from ddtrace._trace.span import Span
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._config import config


log = get_logger(__name__)

# Which attributes hold the status code and the request path depends on the semantics mode,
# and the mode cannot change after startup, so resolve the names once. url.path is already
# just the path, which is all from_url ever looks at. Client spans carry url.full instead of
# url.path, so both are read under the flag; from_url parses either shape.
_STATUS_CODE_TAG: str
_PATH_SOURCE_TAGS: tuple[str, ...]
if config._otel_trace_semantics_enabled:
    _STATUS_CODE_TAG = http.OTEL_RESPONSE_STATUS_CODE
    _PATH_SOURCE_TAGS = (http.OTEL_URL_PATH, http.OTEL_URL_FULL)
else:
    _STATUS_CODE_TAG = http.STATUS_CODE
    _PATH_SOURCE_TAGS = (http.URL,)


def path_source_tag_value(span: Span) -> Optional[str]:
    """The URL-ish tag an endpoint is computed from, in the active semantics mode.

    Server spans carry url.path and client spans carry url.full under the flag, and
    SimplifiedEndpointComputer.from_url parses either shape.
    """
    return next((value for value in map(span.get_tag, _PATH_SOURCE_TAGS) if value), None)


class SimplifiedEndpointComputer:
    def __init__(self) -> None:
        self._INT_RE = re.compile(r"^[1-9][0-9]+$")
        self._INT_ID_RE = re.compile(r"^(?=.*[0-9].*)[0-9._-]{3,}$")
        self._HEX_RE = re.compile(r"^(?=.*[0-9].*)[A-Fa-f0-9]{6,}$")
        self._HEX_ID_RE = re.compile(r"^(?=.*[0-9].*)[A-Fa-f0-9._-]{6,}$")
        self._STR_RE = re.compile(r"^(.{20,}|.*[%&'()*+,:=@].*)$")

    def _compute_simplified_endpoint_path_element(self, elem: str) -> str:
        """Applies the parameter replacement rules to a single path element."""
        if self._INT_RE.fullmatch(elem):
            return "{param:int}"
        if self._INT_ID_RE.fullmatch(elem):
            return "{param:int_id}"
        if self._HEX_RE.fullmatch(elem):
            return "{param:hex}"
        if self._HEX_ID_RE.fullmatch(elem):
            return "{param:hex_id}"
        if self._STR_RE.fullmatch(elem):
            return "{param:str}"
        return elem

    def from_url(self, url: Optional[str]) -> str:
        """Extracts and simplifies the path from an HTTP URL."""
        if not url:
            return "/"

        try:
            parsed_url = urlparse(url)
        except ValueError as e:
            log.error("Failed to parse http.url tag when processing span for resource renaming: %s", e)
            return "/"
        path = parsed_url.path
        if not path or path == "/":
            return "/"

        elements: list[str] = []
        for part in path.split("/"):
            if part:
                elements.append(part)
                if len(elements) >= 8:
                    break

        if not elements:
            return "/"

        elements = [self._compute_simplified_endpoint_path_element(elem) for elem in elements]
        return "/" + "/".join(elements)


class ResourceRenamingProcessor(SpanProcessor):
    def __init__(self):
        self.simplified_endpoint_computer = SimplifiedEndpointComputer()

    def on_span_start(self, span: Span):
        pass

    def on_span_finish(self, span: Span):
        if not span._is_top_level or span.span_type not in (SpanTypes.WEB, SpanTypes.HTTP, SpanTypes.SERVERLESS):
            return

        status: Union[str, int, float, None] = span.get_tag(_STATUS_CODE_TAG)
        if status is None:
            # under OTLP export the status code is written with its integer type
            status = span.get_metric(_STATUS_CODE_TAG)
        is_404 = status == "404" or status == 404

        route = span.get_tag(http.ROUTE)

        if (not is_404 or config._otel_trace_semantics_enabled) and (
            not route or config._trace_resource_renaming_always_simplified_endpoint
        ):
            url = path_source_tag_value(span)
            endpoint = self.simplified_endpoint_computer.from_url(url)
            span._set_attribute(http.ENDPOINT, endpoint)
