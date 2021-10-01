from typing import Dict
from typing import FrozenSet
from typing import Optional

from ddtrace.utils.cache import cached


@cached()
def get_wsgi_header(header):
    # type: (str) -> str
    """Returns a WSGI compliant HTTP header.
    See https://www.python.org/dev/peps/pep-3333/#environ-variables for
    information from the spec.
    """
    return "HTTP_{}".format(header.upper().replace("-", "_"))


@cached()
def from_wsgi_header(header):
    # type: (str) -> Optional[str]
    """Convert a WSGI compliant HTTP header into the original header.
    See https://www.python.org/dev/peps/pep-3333/#environ-variables for
    information from the spec.
    """
    HTTP_PREFIX = "HTTP_"
    # PEP 333 gives two headers which aren't prepended with HTTP_.
    UNPREFIXED_HEADERS = {"CONTENT_TYPE", "CONTENT_LENGTH"}

    if header.startswith(HTTP_PREFIX):
        header = header[len(HTTP_PREFIX) :]
    elif header not in UNPREFIXED_HEADERS:
        return None
    return header.replace("_", "-").title()


def extract_header_value(possible_header_names, headers, default=None):
    # type: (FrozenSet[str], Dict[str, str], Optional[str]) -> Optional[str]
    """Search through a list of possible header names, return the value of the first match"""
    for header in possible_header_names:
        try:
            return headers[header]
        except KeyError:
            pass

    return default
