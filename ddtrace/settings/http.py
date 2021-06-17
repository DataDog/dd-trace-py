from typing import List
from typing import Optional
from typing import Set
from typing import Union

from ..internal.logger import get_logger
from ..utils.cache import cachedmethod
from ..utils.http import normalize_header_name


log = get_logger(__name__)


class HttpConfig(object):
    """
    Configuration object that expose an API to set and retrieve both global and integration specific settings
    related to the http context.
    """

    def __init__(self):
        # type: () -> None
        self._whitelist_headers = set()  # type: Set[str]
        self.trace_query_string = None

    @property
    def is_header_tracing_configured(self):
        # type: () -> bool
        return len(self._whitelist_headers) > 0

    def trace_headers(self, whitelist):
        # type: (Union[List[str], str]) -> Optional[HttpConfig]
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
            self._whitelist_headers.add(normalized_header_name)

        # Mypy can't catch cached method's invalidate()
        self.header_is_traced.invalidate()  # type: ignore[attr-defined]

        return self

    @cachedmethod()
    def header_is_traced(self, header_name):
        # type: (str) -> bool
        """
        Returns whether or not the current header should be traced.
        :param header_name: the header name
        :type header_name: str
        :rtype: bool
        """
        if not self._whitelist_headers:
            return False

        normalized_header_name = normalize_header_name(header_name)
        log.debug("Checking header '%s' tracing in whitelist %s", normalized_header_name, self._whitelist_headers)
        return normalized_header_name in self._whitelist_headers

    def __repr__(self):
        return "<{} traced_headers={} trace_query_string={}>".format(
            self.__class__.__name__, self._whitelist_headers, self.trace_query_string
        )
