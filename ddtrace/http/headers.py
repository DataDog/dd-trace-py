import re


REQUEST = 'request'
RESPONSE = 'response'


def store_request_headers(headers, span, white_list):
    """
    Store request headers as a span's tags
    :param headers: All the request's http headers, will be filtered through the whitelist
    :dict headers: dict
    :param span: The Span instance where tags will be store
    :type span: ddtrace.Span
    """
    _store_headers(headers, span, white_list, REQUEST)


def store_response_headers(headers, span, white_list):
    """
    Store request headers as a span's tags
    :param headers: All the response's http headers, will be filtered through the whitelist
    :dict headers: dict
    :param span: The Span instance where tags will be store
    :type span: ddtrace.Span
    """
    _store_headers(headers, span, white_list, RESPONSE)


def _store_headers(headers, span, white_list, request_or_response):
    """
    :param headers: A callback that provides http headers to be stored in the span
    :param span: The Span instance where tags will be store
    :type span: ddtrace.span.Span
    :param request_or_response: The context of the headers: request|response
    """
    headers = headers  # type: dict
    if not isinstance(headers, dict):
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
    normalized_name = re.sub(r'([^a-z0-9])+', '_',  header_name.strip().lower())
    return 'http.{}.headers.{}'.format(request_or_response, normalized_name)


def _is_white_listed(header_name, white_list):
    for white_list_entry in white_list:
        # White list can use basic * substitution
        if re.match(white_list_entry.strip().lower().replace('*', '.*'), header_name.strip().lower()):
            return True
    return False
