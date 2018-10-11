import re


REQUEST = 'request'
RESPONSE = 'response'


def store_request_headers(headers_provider, span):
    """
    Store request headers as a span's tags
    :param headers_provider: A callback that provides http headers to be stored in the span
    :param span: The Span instance where tags will be store
    :type span: ddtrace.Span
    """
    _store_headers(headers_provider, span, REQUEST)


def store_response_headers(headers_provider, span):
    """
    Store response headers as a span's tags
    :param headers_provider: A callback that provides http headers to be stored in the span
    :param span: The Span instance where tags will be store
    :type span: ddtrace.Span
    """
    _store_headers(headers_provider, span, RESPONSE)


def _store_headers(headers_provider, span, request_or_response):
    """
    :param headers_provider: A callback that provides http headers to be stored in the span
    :param span: The Span instance where tags will be store
    :type span: ddtrace.span.Span
    :param request_or_response: The context of the headers: request|response
    """
    headers = headers_provider()  # type: dict
    if not isinstance(headers, dict):
        return

    for header_name, header_value in headers.items():
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
