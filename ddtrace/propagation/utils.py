def get_wsgi_header(header):
    """Returns a WSGI compliant HTTP header.
    See https://www.python.org/dev/peps/pep-3333/#environ-variables for
    information from the spec.
    """
    return "HTTP_{}".format(header.upper().replace("-", "_"))


_CACHE_HEADER_MAX_SIZE = 256
_cached_headers = {}


def _shrink_cache():
    for _, h in zip(range(_CACHE_HEADER_MAX_SIZE >> 1), sorted(_cached_headers, key=lambda h: _cached_headers[h][1])):
        del _cached_headers[h]


def from_wsgi_header(header):
    """Convert a WSGI compliant HTTP header into the original header.
    See https://www.python.org/dev/peps/pep-3333/#environ-variables for
    information from the spec.
    """
    # Ensure the cache does not grow indefinitely
    if len(_cached_headers) >= _CACHE_HEADER_MAX_SIZE:
        _shrink_cache()

    _ = _cached_headers.get(header)
    if _ is not None:
        value, count = _
        _cached_headers[header] = (value, count + 1)
        return value

    HTTP_PREFIX = "HTTP_"
    # PEP 333 gives two headers which aren't prepended with HTTP_.
    UNPREFIXED_HEADERS = {"CONTENT_TYPE", "CONTENT_LENGTH"}

    original_header = header
    if header.startswith(HTTP_PREFIX):
        header = header[len(HTTP_PREFIX) :]
    elif header not in UNPREFIXED_HEADERS:
        _cached_headers[header] = (None, 1)
        return None
    result = header.replace("_", "-").title()
    _cached_headers[original_header] = (result, 1)
    return result
