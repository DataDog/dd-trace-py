import re
from typing import Optional
from urllib.parse import urlparse

from ddtrace.ext import http
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import SpanData
from ddtrace.internal.otel_semantics import http as otel_http
from ddtrace.internal.settings._config import config


log = get_logger(__name__)


# Under OTel semantics, spans not yet converted (for example server spans) still carry http.url.
def path_source_tag_value(span: SpanData) -> Optional[str]:
    path_source_tags = (
        (otel_http.URL_PATH, otel_http.URL_FULL, http.URL) if config._otel_trace_semantics_enabled else (http.URL,)
    )
    return next((value for value in map(span._get_str_attribute, path_source_tags) if value), None)


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

        if config._otel_trace_semantics_enabled and url.startswith("/"):
            # url.path is already a path; urlparse would read a leading "//" as an authority. A relative
            # http.url fallback may still carry a query or fragment, which url.path never contains.
            path = url.split("?", 1)[0].split("#", 1)[0]
        else:
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
