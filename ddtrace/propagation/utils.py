def get_wsgi_header(header):
    """Returns a WSGI compliant HTTP header.
    See https://www.python.org/dev/peps/pep-3333/#environ-variables for
    information from the spec.
    """
    return "HTTP_{}".format(header.upper().replace("-", "_"))


_cached_headers = {}


def from_wsgi_header(header):
    """Convert a WSGI compliant HTTP header into the original header.
    See https://www.python.org/dev/peps/pep-3333/#environ-variables for
    information from the spec.
    """
    try:
        return _cached_headers[header]
    except KeyError:
        HTTP_PREFIX = "HTTP_"
        # PEP 333 gives two headers which aren't prepended with HTTP_.
        UNPREFIXED_HEADERS = {"CONTENT_TYPE", "CONTENT_LENGTH"}

        original_header = header
        if header.startswith(HTTP_PREFIX):
            header = header[len(HTTP_PREFIX):]
        elif header not in UNPREFIXED_HEADERS:
            _cached_headers[header] = None
            return None
        result = header.replace("_", "-").title()
        _cached_headers[original_header] = result
        return result
