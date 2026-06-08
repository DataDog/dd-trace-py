from typing import TYPE_CHECKING
from typing import Any
from typing import Mapping
from typing import Optional

from ray.serve._private.proxy_request_response import ProxyRequest
from ray.serve._private.proxy_router import ProxyRouter

from ddtrace.contrib.internal.asgi.utils import extract_headers
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import Context


if TYPE_CHECKING:
    from ray.serve.grpc_util import RayServegRPCContext


def extract_grpc_context(headers: Optional["RayServegRPCContext"]) -> Context:
    # `headers` is None when calling the deployment handle remote, which is used in
    # the integration tests to reconfigure() (used in the throttling tests)
    metadata = dict(headers.invocation_metadata()) if headers is not None else {}
    return HTTPPropagator.extract(metadata)


def inject_grpc_context(span_context: Context, headers: Optional["RayServegRPCContext"]) -> None:
    # `headers` is None when calling the deployment handle remote, which is used in
    # the integration tests to reconfigure() (used in the throttling tests)
    if headers is None:
        return

    trace_headers: dict[str, str] = {}
    HTTPPropagator.inject(span_context, trace_headers)

    invocation_metadata = list(headers.invocation_metadata())
    invocation_metadata.extend(trace_headers.items())
    headers._invocation_metadata = invocation_metadata


def _extract_proxy_request_http_context(proxy_request: ProxyRequest) -> Context:
    try:
        headers = extract_headers(getattr(proxy_request, "scope", {}))
    except Exception:
        headers = {}

    extracted_context = HTTPPropagator.extract(headers)
    return extracted_context


def _get_proxy_request_route_pattern(instance, proxy_request: ProxyRequest) -> Optional[str]:
    proxy_router: ProxyRouter = instance.proxy_router
    scope = getattr(proxy_request, "scope", None)
    if not isinstance(scope, dict):
        return None
    asgi_scope: dict[str, Any] = scope

    matched_route = proxy_router.match_route(proxy_request.route_path)
    if not matched_route:
        return None

    route_prefix = matched_route[0]

    # match_route_pattern allow to show templated route for instance
    # /name/foo -> /name/{name}
    # match_route_pattern is not available in ray 2.46.0, therefore we will only be
    # able to show the route_prefix to prevent high cardinality resource name
    match_route_pattern = getattr(proxy_router, "match_route_pattern", None)
    if callable(match_route_pattern):
        return match_route_pattern(route_prefix, asgi_scope)
    return route_prefix


def _get_ingress_endpoint_method_name(scope: Optional[Mapping[str, object]]) -> Optional[str]:
    if scope is None:
        return None

    endpoint = scope.get("endpoint")
    if endpoint is None:
        return None

    endpoint_name = getattr(endpoint, "__name__", None)
    if not endpoint_name or endpoint_name == "__call__":
        return None
    return endpoint_name
