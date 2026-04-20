from typing import Mapping  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Union  # noqa:F401

from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import get_config as _get_config
from ddtrace.internal.utils.cache import cachedmethod
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.http import normalize_header_name
from ddtrace.vendor.debtcollector import deprecate


log = get_logger(__name__)


def get_error_ranges(error_range_str: str) -> list[tuple[int, int]]:
    error_ranges = []
    error_range_str = error_range_str.strip()
    error_ranges_str = error_range_str.split(",")
    for error_range in error_ranges_str:
        values = error_range.split("-")
        try:
            # Note: mypy does not like variable type changing
            values = [int(v) for v in values]  # type: ignore[misc]
        except ValueError:
            log.exception("Error status codes was not a number %s", values)
            continue
        error_range = (min(values), max(values))  # type: ignore[assignment]
        error_ranges.append(error_range)
    return error_ranges  # type: ignore[return-value]


class _HTTPBaseConfig(object):
    _error_statuses: str
    _error_ranges: list[tuple[int, int]]

    @property
    def error_statuses(self) -> str:
        return self._error_statuses

    @error_statuses.setter
    def error_statuses(self, value: str) -> None:
        self._error_statuses = value
        self._error_ranges = get_error_ranges(value)
        # Mypy can't catch cached method's invalidate()
        self.is_error_code.cache_clear()  # type: ignore[attr-defined]

    @property
    def error_ranges(self) -> list[tuple[int, int]]:
        return self._error_ranges

    @cachedmethod()
    def is_error_code(self, status_code: int) -> bool:
        """Returns a boolean representing whether or not a status code is an error code."""
        for error_range in self.error_ranges:
            if error_range[0] <= status_code <= error_range[1]:
                return True
        return False


class _HTTPServerConfig(_HTTPBaseConfig):
    _error_statuses: str = _get_config("DD_TRACE_HTTP_SERVER_ERROR_STATUSES", "500-599")
    _error_ranges: list[tuple[int, int]] = get_error_ranges(_error_statuses)


class _HTTPClientConfig(_HTTPBaseConfig):
    def __init__(self) -> None:
        _unset = object()
        client_configured = _get_config("DD_TRACE_HTTP_CLIENT_ERROR_STATUSES", _unset, report_telemetry=False)
        if client_configured is not _unset:
            self._error_statuses = _get_config("DD_TRACE_HTTP_CLIENT_ERROR_STATUSES", "500-599")
        else:
            server_configured = _get_config("DD_TRACE_HTTP_SERVER_ERROR_STATUSES", _unset, report_telemetry=False)
            if server_configured is not _unset:
                deprecate(
                    "DD_TRACE_HTTP_SERVER_ERROR_STATUSES is being applied to HTTP client spans.",
                    message=(
                        "Set DD_TRACE_HTTP_CLIENT_ERROR_STATUSES explicitly to configure client span error statuses. "
                        "In a future version, client spans will default to '500-599' independently of "
                        "DD_TRACE_HTTP_SERVER_ERROR_STATUSES."
                    ),
                    removal_version="5.0.0",
                    category=DDTraceDeprecationWarning,
                )
            self._error_statuses = _get_config("DD_TRACE_HTTP_SERVER_ERROR_STATUSES", "500-599")
        self._error_ranges = get_error_ranges(self._error_statuses)


class HttpConfig(object):
    """
    Configuration object that expose an API to set and retrieve both global and integration specific settings
    related to the http context.
    """

    def __init__(self, header_tags: Optional[Mapping[str, str]] = None) -> None:
        self._header_tags = {normalize_header_name(k): v for k, v in header_tags.items()} if header_tags else {}
        self.trace_query_string = None

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
