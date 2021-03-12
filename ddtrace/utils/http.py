def normalize_header_name(header_name):
    """
    Normalizes an header name to lower case, stripping all its leading and trailing white spaces.
    :param header_name: the header name to normalize
    :type header_name: str
    :return: the normalized header name
    :rtype: str
    """
    return header_name.strip().lower() if header_name is not None else None


def strip_query_string(url):
    """
    Strips the query string from a URL for use as tag in spans.
    :param url: The URL to be stripped
    :return: The given URL without query strings
    """
    # type: (str) -> str
    hqs, fs, f = url.partition("#")
    h, _, _ = hqs.partition("?")
    if not f:
        return h
    return h + fs + f
