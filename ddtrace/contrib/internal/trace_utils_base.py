import re

from ddtrace.internal.utils.cache import cached
from ddtrace.internal.utils.http import normalize_header_name


NORMALIZE_PATTERN = re.compile(r"([^a-z0-9_\-:/]){1}")


@cached()
def _normalized_header_name(header_name: str) -> str:
    return NORMALIZE_PATTERN.sub("_", normalize_header_name(header_name))


def _normalize_tag_name(request_or_response: str, header_name: str) -> str:
    """
    Given a tag name, e.g. 'Content-Type', returns a corresponding normalized tag name, i.e
    'http.request.headers.content_type'. Rules applied actual header name are:
    - any letter is converted to lowercase
    - any digit is left unchanged
    - any block of any length of different ASCII chars is converted to a single underscore '_'
    :param request_or_response: The context of the headers: request|response
    :param header_name: The header's name
    :type header_name: str
    :rtype: str
    """
    # Looking at:
    #   - http://www.iana.org/assignments/message-headers/message-headers.xhtml
    #   - https://tools.ietf.org/html/rfc6648
    # and for consistency with other language integrations seems safe to assume the following algorithm for header
    # names normalization:
    #   - any letter is converted to lowercase
    #   - any digit is left unchanged
    #   - any block of any length of different ASCII chars is converted to a single underscore '_'
    normalized_name = _normalized_header_name(header_name)
    return "http.{}.headers.{}".format(request_or_response, normalized_name)
