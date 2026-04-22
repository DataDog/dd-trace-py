from ddtrace.internal.compat import ensure_binary
from ddtrace.internal.compat import ensure_text


def httpx_url_to_str(url) -> str:
    """
    Helper to convert the httpx.URL parts from bytes to a str
    """
    scheme = url.raw_scheme
    host = url.raw_host
    port = url.port
    raw_path = url.raw_path
    url = scheme + b"://" + host
    if port is not None:
        url += b":" + ensure_binary(str(port))
    url += raw_path

    return ensure_text(url)
