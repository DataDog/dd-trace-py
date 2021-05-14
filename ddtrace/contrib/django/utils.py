from django.utils.functional import SimpleLazyObject
from six import ensure_text

from ...internal.logger import get_logger
from .compat import get_resolver


log = get_logger(__name__)


def resource_from_cache_prefix(resource, cache):
    """
    Combine the resource name with the cache prefix (if any)
    """
    if getattr(cache, "key_prefix", None):
        name = " ".join((resource, cache.key_prefix))
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


def get_django_2_route(request, resolver_match):
    # Try to use `resolver_match.route` if available
    # Otherwise, look for `resolver.pattern.regex.pattern`
    route = resolver_match.route
    if route:
        return route

    resolver = get_resolver(getattr(request, "urlconf", None))
    if resolver:
        try:
            return resolver.pattern.regex.pattern
        except AttributeError:
            pass

    return None


def set_tag_array(span, prefix, value):
    """Helper to set a span tag as a single value or an array"""
    if not value:
        return

    if len(value) == 1:
        if value[0]:
            span._set_str_tag(prefix, value[0])
    else:
        for i, v in enumerate(value, start=0):
            if v:
                span._set_str_tag("".join((prefix, ".", str(i))), v)


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
                    host = "".join((host, ":", port))
        except Exception:
            # This really shouldn't ever happen, but lets guard here just in case
            log.debug("Failed to build Django request host", exc_info=True)
            host = "unknown"

    # If request scheme is missing, possible in case where wsgi.url_scheme
    # environ has not been set, return None and skip providing a uri
    if request.scheme is None:
        return

    # Build request url from the information available
    # DEV: We are explicitly omitting query strings since they may contain sensitive information
    urlparts = {"scheme": request.scheme, "netloc": host, "path": request.path}

    # If any url part is a SimpleLazyObject, use its __class__ property to cast
    # str/bytes and allow for _setup() to execute
    for (k, v) in urlparts.items():
        if isinstance(v, SimpleLazyObject):
            if issubclass(v.__class__, str):
                v = str(v)
            elif issubclass(v.__class__, bytes):
                v = bytes(v)
            else:
                # lazy object that is not str or bytes should not happen here
                # but if it does skip providing a uri
                log.debug(
                    "Skipped building Django request uri, %s is SimpleLazyObject wrapping a %s class",
                    k,
                    v.__class__.__name__,
                )
                return None
        urlparts[k] = ensure_text(v)

    return "".join((urlparts["scheme"], "://", urlparts["netloc"], urlparts["path"]))
