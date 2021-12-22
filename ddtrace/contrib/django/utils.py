from django.utils.functional import SimpleLazyObject
import six

from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib import func_name
from ddtrace.ext import SpanTypes
from ddtrace.propagation._utils import from_wsgi_header

from .. import trace_utils
from ...internal.logger import get_logger
from .compat import get_resolver
from .compat import user_is_authenticated


log = get_logger(__name__)


# Set on patch, when django is imported
Resolver404 = None
DJANGO22 = None

REQUEST_DEFAULT_RESOURCE = "__django_request"


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
        urlparts[k] = six.ensure_text(v)

    return "".join((urlparts["scheme"], "://", urlparts["netloc"], urlparts["path"]))


def _set_resolver_tags(pin, span, request):
    # Default to just the HTTP method when we cannot determine a reasonable resource
    resource = request.method

    try:
        # Get resolver match result and build resource name pieces
        resolver_match = request.resolver_match
        if not resolver_match:
            # The request quite likely failed (e.g. 404) so we do the resolution anyway.
            resolver = get_resolver(getattr(request, "urlconf", None))
            resolver_match = resolver.resolve(request.path_info)
        handler = func_name(resolver_match[0])

        if config.django.use_handler_resource_format:
            resource = " ".join((request.method, handler))
        elif config.django.use_legacy_resource_format:
            resource = handler
        else:
            # In Django >= 2.2.0 we can access the original route or regex pattern
            # TODO: Validate if `resolver.pattern.regex.pattern` is available on django<2.2
            if DJANGO22:
                # Determine the resolver and resource name for this request
                route = get_django_2_route(request, resolver_match)
                if route:
                    resource = " ".join((request.method, route))
                    span._set_str_tag("http.route", route)
            else:
                resource = " ".join((request.method, handler))

        span._set_str_tag("django.view", resolver_match.view_name)
        set_tag_array(span, "django.namespace", resolver_match.namespaces)

        # Django >= 2.0.0
        if hasattr(resolver_match, "app_names"):
            set_tag_array(span, "django.app", resolver_match.app_names)

    except Resolver404:
        # Normalize all 404 requests into a single resource name
        # DEV: This is for potential cardinality issues
        resource = " ".join((request.method, "404"))
    except Exception:
        log.debug(
            "Failed to resolve request path %r with path info %r",
            request,
            getattr(request, "path_info", "not-set"),
            exc_info=True,
        )
    finally:
        # Only update the resource name if it was not explicitly set
        # by anyone during the request lifetime
        if span.resource == REQUEST_DEFAULT_RESOURCE:
            span.resource = resource


def _before_request_tags(pin, span, request):
    # DEV: Do not set `span.resource` here, leave it as `None`
    #      until `_set_resolver_tags` so we can know if the user
    #      has explicitly set it during the request lifetime
    span.service = trace_utils.int_service(pin, config.django)
    span.span_type = SpanTypes.WEB
    span.metrics[SPAN_MEASURED_KEY] = 1

    analytics_sr = config.django.get_analytics_sample_rate(use_global_config=True)
    if analytics_sr is not None:
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, analytics_sr)

    span._set_str_tag("django.request.class", func_name(request))


def _after_request_tags(pin, span, request, response):
    # Response can be None in the event that the request failed
    # We still want to set additional request tags that are resolved
    # during the request.

    try:
        user = getattr(request, "user", None)
        if user is not None:
            # Note: getattr calls to user / user_is_authenticated may result in ImproperlyConfigured exceptions from
            # Django's get_user_model():
            # https://github.com/django/django/blob/a464ead29db8bf6a27a5291cad9eb3f0f3f0472b/django/contrib/auth/__init__.py
            try:
                if hasattr(user, "is_authenticated"):
                    span._set_str_tag("django.user.is_authenticated", str(user_is_authenticated(user)))

                uid = getattr(user, "pk", None)
                if uid:
                    span._set_str_tag("django.user.id", str(uid))

                if config.django.include_user_name:
                    username = getattr(user, "username", None)
                    if username:
                        span._set_str_tag("django.user.name", username)
            except Exception:
                log.debug("Error retrieving authentication information for user %r", user, exc_info=True)

        if response:
            status = response.status_code
            span._set_str_tag("django.response.class", func_name(response))
            if hasattr(response, "template_name"):
                # template_name is a bit of a misnomer, as it could be any of:
                # a list of strings, a tuple of strings, a single string, or an instance of Template
                # for more detail, see:
                # https://docs.djangoproject.com/en/3.0/ref/template-response/#django.template.response.SimpleTemplateResponse.template_name
                template = response.template_name

                if isinstance(template, six.string_types):
                    template_names = [template]
                elif isinstance(
                    template,
                    (
                        list,
                        tuple,
                    ),
                ):
                    template_names = template
                elif hasattr(template, "template"):
                    # ^ checking by attribute here because
                    # django backend implementations don't have a common base
                    # `.template` is also the most consistent across django versions
                    template_names = [template.template.name]
                else:
                    template_names = None

                set_tag_array(span, "django.response.template", template_names)

            url = get_request_uri(request)

            if DJANGO22:
                request_headers = request.headers
            else:
                request_headers = {}
                for header, value in request.META.items():
                    name = from_wsgi_header(header)
                    if name:
                        request_headers[name] = value

            # DEV: Resolve the view and resource name at the end of the request in case
            #      urlconf changes at any point during the request
            _set_resolver_tags(pin, span, request)

            response_headers = dict(response.items()) if response else {}
            trace_utils.set_http_meta(
                span,
                config.django,
                method=request.method,
                url=url,
                status_code=status,
                query=request.META.get("QUERY_STRING", None),
                request_headers=request_headers,
                response_headers=response_headers,
            )
    finally:
        if span.resource == REQUEST_DEFAULT_RESOURCE:
            span.resource = request.method
