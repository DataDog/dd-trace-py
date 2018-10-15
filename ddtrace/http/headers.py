import re
from ..utils.http import normalize_header_name


REQUEST = 'request'
RESPONSE = 'response'

NORMALIZE_PATTERN = re.compile(r'([^a-z0-9])+')


def store_request_headers(headers, span, white_list):
    """
    Store request headers as a span's tags
    :param headers: All the request's http headers, will be filtered through the whitelist
    :type headers: dict
    :param span: The Span instance where tags will be store
    :type span: ddtrace.Span
    :param white_list: the list of white listed names. Accepts '*' meaning 'anything'.
    :type white_list: list of str
    """
    _store_headers(headers, span, white_list, REQUEST)


def store_response_headers(headers, span, white_list):
    """
    Store request headers as a span's tags
    :param headers: All the response's http headers, will be filtered through the whitelist
    :type headers: dict
    :param span: The Span instance where tags will be store
    :type span: ddtrace.Span
    :param white_list: the list of white listed names. Accepts '*' meaning 'anything'.
    :type white_list: list of str
    """
    _store_headers(headers, span, white_list, RESPONSE)


def _store_headers(headers, span, white_list, request_or_response):
    """
    :param headers: A dict of http headers to be stored in the span
    :type headers: dict
    :param span: The Span instance where tags will be store
    :type span: ddtrace.span.Span
    :param white_list: the list of white listed names. Accepts '*' meaning 'anything'.
    :type white_list: list of str
    :param request_or_response: The context of the headers: request|response
    """
    if not isinstance(headers, dict):
        return

    if not white_list:
        return

    for header_name, header_value in headers.items():
        if not _is_white_listed(header_name, white_list):
            continue
        tag_name = _normalize_tag_name(request_or_response, header_name)
        span.set_tag(tag_name, header_value)


def _normalize_tag_name(request_or_response, header_name):
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
    normalized_name = NORMALIZE_PATTERN.sub('_', normalize_header_name(header_name))
    return 'http.{}.headers.{}'.format(request_or_response, normalized_name)


def _is_white_listed(header_name, white_list):
    """
    Tells whether or not an header name is white listed. Accepts '*' meaning 'anything'.
    :param header_name: the header name to check for
    :type header_name: str
    :param white_list: the list of white listed names. Accepts '*' meaning 'anything'.
    :type white_list: list of str
    :rtype: bool
    """
    normalized_header_name = normalize_header_name(header_name)
    for white_list_entry in white_list:
        normalized_white_list_entry = normalize_header_name(white_list_entry)
        if white_list_entry == '*' or normalized_white_list_entry == normalized_header_name:
            return True
        # White list can use basic * substitution. Note that this works because headers names do not have any special
        # character in them, otherwise we should escape the names as regex.
        elif re.match(normalize_header_name(white_list_entry).replace('*', '.*'), normalize_header_name(header_name)):
            return True
    return False
