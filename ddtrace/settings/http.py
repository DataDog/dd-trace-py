from typing import List
from typing import Optional
from typing import Set
from typing import Union

from ..internal.logger import get_logger
from ..utils.http import normalize_header_name


log = get_logger(__name__)


class HttpConfig(object):
    """
    Configuration object that expose an API to set and retrieve both global and integration specific settings
    related to the http context.
    """

    def __init__(self):
        # type: () -> None
        self.traced_headers = set()  # type: Set[str]
        self.trace_query_string = None

    @property
    def is_header_tracing_configured(self):
        # type: () -> bool
        return len(self.traced_headers) > 0

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
            self.traced_headers.add(normalized_header_name)

        return self

    def __repr__(self):
        return "<{} traced_headers={} trace_query_string={}>".format(
            self.__class__.__name__, self.traced_headers, self.trace_query_string
        )
