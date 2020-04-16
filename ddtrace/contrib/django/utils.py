from ...compat import parse
from ...internal.logger import get_logger


log = get_logger(__name__)


def resource_from_cache_prefix(resource, cache):
    """
    Combine the resource name with the cache prefix (if any)
    """
    if getattr(cache, "key_prefix", None):
        name = "{} {}".format(resource, cache.key_prefix)
    else:
        name = resource

    # enforce lowercase to make the output nicer to read
    return name.lower()


def quantize_key_values(key):
    """
    Used in the Django trace operation method, it ensures that if a dict
    with values is used, we removes the values from the span meta
    attributes. For example::

        >>> quantize_key_values({'key': 'value'})
        # returns ['key']
    """
    if isinstance(key, dict):
        return key.keys()

    return key


def get_django_2_route(resolver, resolver_match):
    # Try to use `resolver_match.route` if available
    # Otherwise, look for `resolver.pattern.regex.pattern`
    route = resolver_match.route
    if not route:
        # DEV: Use all these `getattr`s to protect against changes between versions
        pattern = getattr(resolver, "pattern", None)
        if pattern:
            regex = getattr(pattern, "regex", None)
            if regex:
                route = getattr(regex, "pattern", "")

    return route


def set_tag_array(span, prefix, value):
    """Helper to set a span tag as a single value or an array"""
    if not value:
        return

    if len(value) == 1:
        span.set_tag(prefix, value[0])
    else:
        for i, v in enumerate(value, start=0):
            span.set_tag("{0}.{1}".format(prefix, i), v)


def get_request_uri(request):
    """
    Helper to rebuild the original request url

    query string or fragments are not included.
    """
    # DEV: Use django.http.request.HttpRequest._get_raw_host() when available
    # otherwise back-off to PEP 333 as done in django 1.8.x
    if hasattr(request, "_get_raw_host"):
        host = request._get_raw_host()
    else:
        try:
            # Try to build host how Django would have
            # https://github.com/django/django/blob/e8d0d2a5efc8012dcc8bf1809dec065ebde64c81/django/http/request.py#L85-L102
            if "HTTP_HOST" in request.META:
                host = request.META["HTTP_HOST"]
            else:
                host = request.META["SERVER_NAME"]
                port = str(request.META["SERVER_PORT"])
                if port != ("443" if request.is_secure() else "80"):
                    host = "{0}:{1}".format(host, port)
        except Exception:
            # This really shouldn't ever happen, but lets guard here just in case
            log.debug("Failed to build Django request host", exc_info=True)
            host = "unknown"

    # Build request url from the information available
    # DEV: We are explicitly omitting query strings since they may contain sensitive information
    return parse.urlunparse(
        parse.ParseResult(scheme=request.scheme, netloc=host, path=request.path, params="", query="", fragment="",)
    )
