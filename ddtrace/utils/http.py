from ..compat import parse


def normalize_header_name(header_name):
    """
    Normalizes an header name to lower case, stripping all its leading and trailing white spaces.
    :param header_name: the header name to normalize
    :type header_name: str
    :return: the normalized header name
    :rtype: str
    """
    return header_name.strip().lower() if header_name is not None else None


def sanitize_url_for_tag(url):
    """
    Strips the qs from a URL for use as tag in spans.

    :param url: The url to be stripped
    :type url: str
    :return: The sanitized URL
    :rtype: str
    """
    parsed = parse.urlparse(url)
    return parse.urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            None,  # drop parsed.query
            parsed.fragment,
        )
    )
