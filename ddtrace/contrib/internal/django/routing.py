from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Iterable
from typing import Optional
from typing import Union
import weakref

from ddtrace.internal.endpoints import endpoint_collection
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.appsec_telemetry import config as appsec_telemetry_config


if TYPE_CHECKING:
    from django.urls.resolvers import URLPattern
    from django.urls.resolvers import URLResolver


log = get_logger(__name__)


def extract_request_method_list(view):
    try:
        while "view_func" in view.__code__.co_freevars:
            view = view.__closure__[view.__code__.co_freevars.index("view_func")].cell_contents
        if "request_method_list" in view.__code__.co_freevars:
            return view.__closure__[view.__code__.co_freevars.index("request_method_list")].cell_contents
        return []
    except Exception:
        return []


# Resolvers whose url_patterns tree has already been walked for endpoint
# collection by _collect_routes_once(). A WeakSet lets entries auto-drop when
# Django releases a resolver (e.g. after clear_url_caches()), which removes any
# id-reuse risk that a plain set[int] would carry.
_collected_resolvers: "weakref.WeakSet[URLResolver]" = weakref.WeakSet()


def _collect_pattern_methods(callback: Optional[Callable[..., Any]]) -> list[str]:
    """Return the HTTP methods a URLPattern.callback handles, for endpoint collection.

    Extraction semantics: extract_request_method_list walks the wrapper's
    closure chain itself (via the view_func freevar), so the outer callback is
    passed as-is. Unwrapping via __wrapped__ first would peel past the
    require_http_methods wrapper and lose the captured request_method_list,
    collapsing the recorded method list to a wildcard.
    """
    if callback is None:
        return ["*"]
    http_method_names = getattr(callback, "http_method_names", ())
    request_method_list = extract_request_method_list(callback) or http_method_names
    return list(request_method_list) or ["*"]


def _collect_django_routes(patterns: "Iterable[Union[URLPattern, URLResolver]]", prefix: str = "") -> None:
    """Walk URLPattern / URLResolver nodes and register endpoints in endpoint_collection.

    Joins parent and child route segments with the same semantics Django
    itself uses in django.urls.resolvers.URLResolver._join_route for
    request.resolver_match.route: the leading ``^`` of a regex child is
    dropped when appending onto a non-empty prefix, so mixed re_path/path
    trees produce the same route string Django exposes at runtime.
    Non-URLPattern/URLResolver nodes are skipped (e.g. channels URLRouter
    entries slipped into a resolver tree).
    """
    from django.urls.resolvers import URLPattern
    from django.urls.resolvers import URLResolver

    for pattern in patterns:
        if not isinstance(pattern, (URLPattern, URLResolver)):
            continue
        segment = str(pattern.pattern)
        if prefix:
            segment = segment.removeprefix("^")
        full_path = prefix + segment
        if isinstance(pattern, URLResolver):
            sub_patterns = getattr(pattern, "url_patterns", None)
            if sub_patterns is None:
                continue
            _collect_django_routes(sub_patterns, prefix=full_path)
        else:
            for method in _collect_pattern_methods(getattr(pattern, "callback", None)):
                endpoint_collection.add_endpoint(method, full_path, operation_name="django.request")


def _collect_routes_once(resolver: "Optional[URLResolver]") -> None:
    """Populate endpoint_collection by walking resolver.url_patterns once per resolver.

    Called from traced_load_middleware when a request handler is built, and from traced_get_response /
    traced_get_response_async on every request; the walk itself happens once per distinct resolver rather than once
    per call site. The WeakSet gate makes repeated calls O(1), and naturally handles
    per-request request.urlconf swaps (each distinct urlconf gets its own resolver from django.urls.get_resolver,
    walked on first use). When the endpoint-collection flag is off, the walk is skipped entirely — telemetry would
    discard the collected entries anyway, and if the flag is later flipped on the WeakSet stays empty so the next
    request will walk.
    """
    if resolver is None or not appsec_telemetry_config.ENDPOINT_COLLECTION_ENABLED:
        return
    try:
        if resolver in _collected_resolvers:
            return
    except TypeError:
        # Unhashable / unreferenceable resolver shouldn't happen for
        # django.urls.URLResolver, but guard against exotic custom types.
        return
    try:
        patterns = getattr(resolver, "url_patterns", None)
        if patterns is None:
            return
        _collect_django_routes(patterns)
    except Exception:
        log.debug("Failed to walk Django URL resolver for endpoint collection", exc_info=True)
    finally:
        # Mark as collected even on failure so we don't retry forever on a
        # malformed urlconf. A restart recovers; a transient error is a bug
        # we'd rather notice once via log.debug than spam every request.
        try:
            _collected_resolvers.add(resolver)
        except TypeError:
            pass
