from collections.abc import Mapping  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Union  # noqa:F401

from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.utils.cache import cachedmethod
from ddtrace.internal.utils.http import normalize_header_name


log = get_logger(__name__)


class HttpConfig:
    """
    Configuration object that expose an API to set and retrieve both global and integration specific settings
    related to the http context.
    """

    def __init__(self, header_tags: Optional[Mapping[str, str]] = None) -> None:
        self._header_tags = {normalize_header_name(k): v for k, v in header_tags.items()} if header_tags else {}
        self.trace_query_string: Optional[bool] = None

    def _reset(self):
        self._header_tags = {}
        self._header_tag_name.cache_clear()

    @cachedmethod()
    def _header_tag_name(self, header_name: str) -> Optional[str]:
        if not self._header_tags:
            return None

        normalized_header_name = normalize_header_name(header_name)
        log.debug("Checking header '%s' tracing in whitelist %s", normalized_header_name, self._header_tags.keys())
        return self._header_tags.get(normalized_header_name)

    @property
    def is_header_tracing_configured(self) -> bool:
        return len(self._header_tags) > 0

    def trace_headers(self, whitelist: Union[list[str], str]) -> Optional["HttpConfig"]:
        """
        Registers a set of headers to be traced at global level or integration level.
        :param whitelist: the case-insensitive list of traced headers
        :type whitelist: list of str or str
        :return: self
        :rtype: HttpConfig
        """
        if not whitelist:
            return None

        whitelist = [whitelist] if isinstance(whitelist, str) else whitelist
        for whitelist_entry in whitelist:
            normalized_header_name = normalize_header_name(whitelist_entry)
            if not normalized_header_name:
                continue
            # Empty tag is replaced by the default tag for this header:
            #  Host on the request defaults to http.request.headers.host
            self._header_tags.setdefault(normalized_header_name, "")

        # Mypy can't catch cached method's invalidate()
        self._header_tag_name.cache_clear()  # type: ignore[attr-defined]

        return self

    def header_is_traced(self, header_name: str) -> bool:
        """
        Returns whether or not the current header should be traced.
        :param header_name: the header name
        :type header_name: str
        :rtype: bool
        """
        return self._header_tag_name(header_name) is not None

    def __repr__(self):
        return (
            f"<{self.__class__.__name__} "
            f"traced_headers={self._header_tags.keys()} "
            f"trace_query_string={self.trace_query_string}>"
        )


# Utility functions consuming a (integration-level, tracer-wide) pair of HttpConfig objects to
# answer the questions IntegrationConfig/HttpIntegrationConfigMixin expose as properties. Plain
# functions, not methods, so the logic is reusable/testable independently of what config class holds
# the HttpConfig objects. Deliberately parameter-based rather than reaching for the tracer-wide
# Config singleton themselves: this module is a leaf ddtrace.internal.settings._config already
# imports (for HttpConfig), so it must never import _config.py back. HttpIntegrationConfigMixin,
# which needs the actual Config singleton to supply that second argument, lives in
# ddtrace/internal/integrations.py instead -- see that module for why.


def trace_query_string(http_config: HttpConfig, global_http_config: Optional[HttpConfig]) -> Optional[bool]:
    """Whether to tag query strings for a request, given its integration-level HttpConfig and (if
    available) the tracer-wide one to fall back to when the integration hasn't configured it.
    """
    if http_config.trace_query_string is not None:
        return http_config.trace_query_string
    return global_http_config.trace_query_string if global_http_config is not None else None


def is_header_tracing_configured(http_config: HttpConfig, global_http_config: Optional[HttpConfig]) -> bool:
    """Whether header tracing is enabled, either for this integration specifically or tracer-wide."""
    if global_http_config is not None:
        return http_config.is_header_tracing_configured or global_http_config.is_header_tracing_configured
    return http_config.is_header_tracing_configured


def header_tag_name(
    http_config: HttpConfig, global_http_config: Optional[HttpConfig], header_name: str
) -> Optional[str]:
    # _header_tag_name is a @cachedmethod, whose descriptor type doesn't preserve HttpConfig's own
    # Optional[str] return annotation for mypy.
    tag_name: Optional[str] = http_config._header_tag_name(header_name)
    if tag_name is None and global_http_config is not None:
        return global_http_config._header_tag_name(header_name)  # type: ignore[no-any-return]
    return tag_name


def header_is_traced(http_config: HttpConfig, global_http_config: Optional[HttpConfig], header_name: str) -> bool:
    return header_tag_name(http_config, global_http_config, header_name) is not None


def get_http_tag_query_string(global_http_tag_query_string: bool, value: Optional[str]) -> bool:
    """Whether query strings should be tagged by default for an integration whose own static default
    is `value`, given the tracer-wide default (`Config._http_tag_query_string`).
    """
    if global_http_tag_query_string:
        dd_http_server_tag_query_string = value if value else env.get("DD_HTTP_SERVER_TAG_QUERY_STRING", "true")
        # If invalid value, will default to True
        return dd_http_server_tag_query_string.lower() not in ("false", "0")
    return False
