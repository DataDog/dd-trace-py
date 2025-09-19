import re
from typing import List
from typing import Optional
from urllib.parse import urlparse

from ddtrace._trace.processor import SpanProcessor
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.internal.logger import get_logger
from ddtrace.settings._config import config


log = get_logger(__name__)


class ResourceRenamingProcessor(SpanProcessor):
    def __init__(self):
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

    def _compute_simplified_endpoint(self, url: Optional[str]) -> str:
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

        elements: List[str] = []
        for part in path.split("/"):
            if part:
                elements.append(part)
                if len(elements) >= 8:
                    break

        if not elements:
            return "/"

        elements = [self._compute_simplified_endpoint_path_element(elem) for elem in elements]
        return "/" + "/".join(elements)

    def on_span_start(self, span):
        pass

    def on_span_finish(self, span):
        if not span._is_top_level or span.span_type not in (SpanTypes.WEB, SpanTypes.HTTP, SpanTypes.SERVERLESS):
            return

        route = span.get_tag(http.ROUTE)

        if not route or config._trace_resource_renaming_always_simplified_endpoint:
            url = span.get_tag(http.URL)
            endpoint = self._compute_simplified_endpoint(url)
            span.set_tag_str(http.ENDPOINT, endpoint)
