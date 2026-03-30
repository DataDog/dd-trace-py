from functools import wraps
import inspect
from typing import TYPE_CHECKING
from typing import Optional
from typing import cast

from ray.serve._private.common import RequestMetadata
from ray.serve._private.proxy_request_response import ProxyRequest
from ray.serve._private.proxy_request_response import ResponseStatus
from ray.serve._private.proxy_request_response import gRPCProxyRequest

from ddtrace.contrib.internal.ray.serve.utils import _extract_proxy_request_http_context
from ddtrace.contrib.internal.ray.serve.utils import _get_ingress_endpoint_method_name
from ddtrace.contrib.internal.ray.serve.utils import _get_proxy_request_route_pattern
from ddtrace.contrib.internal.ray.serve.utils import extract_grpc_context
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import Context
from ddtrace.trace import tracer


if TYPE_CHECKING:
    from ray.serve._private.replica import ReplicaBase
    from ray.serve.handle import DeploymentHandle

log = get_logger(__name__)

_DD_REQUEST_METADATA_TRACE_CONTEXT_ATTR = "_dd_trace_context_headers"
_DD_RAY_SERVE_WRAPPED_ATTR = "__dd_ray_serve_wrapped__"

RAY_SERVE_REPLICA_METHOD_DENYLIST = {
    "record_routing_stats",
    "check_health",
    "is_allocated",
    "initialize_and_get_metadata",
}


def _get_serve_request_context_tags() -> dict[str, str]:
    try:
        from ray.serve import context as serve_context

        request_context = serve_context._get_serve_request_context()
    except Exception:
        # Best-effort metadata extraction only.
        return {}

    tags = {}
    request_id = getattr(request_context, "request_id", "")
    app_name = getattr(request_context, "app_name", "")
    route = getattr(request_context, "route", "")
    multiplexed_model_id = getattr(request_context, "multiplexed_model_id", "")

    if request_id:
        tags["ray.serve.request_id"] = request_id
    if app_name:
        tags["ray.serve.app_name"] = app_name
    if route:
        tags["ray.serve.route"] = route
    if multiplexed_model_id:
        tags["ray.serve.multiplexed_model_id"] = multiplexed_model_id

    try:
        replica_context = serve_context._get_internal_replica_context()
        replica_id = replica_context.replica_id.to_full_id_str() if replica_context is not None else ""
    except Exception:
        replica_id = ""

    if replica_id:
        tags["ray.serve.replica_id"] = replica_id

    return tags


def _set_deployment_method_span_metadata(ctx, deployment_name: str, method_name: str) -> None:
    span = ctx.span
    if span is None:
        return

    span._set_attribute("component", "ray")
    span._set_attribute("ray.serve.deployment", deployment_name)
    span._set_attribute("ray.serve.call_method", method_name)

    for tag_name, tag_value in _get_serve_request_context_tags().items():
        span._set_attribute(tag_name, tag_value)


class ServeRequestContextPropagator:
    @staticmethod
    def extract_from_request_metadata(request_meta: RequestMetadata) -> Context | None:
        if request_meta.is_grpc_request:
            return extract_grpc_context(request_meta.grpc_context)

        headers = getattr(request_meta, _DD_REQUEST_METADATA_TRACE_CONTEXT_ATTR, None)
        if isinstance(headers, dict):
            return HTTPPropagator.extract(headers)
        return None


async def traced_assign_request(func, instance, args, kwargs):
    request_meta: RequestMetadata = cast(RequestMetadata, get_argument_value(args, kwargs, 0, "request_meta"))
    context = ServeRequestContextPropagator.extract_from_request_metadata(request_meta)

    with core.context_with_data(
        "ray.assign.request",
        span_name="Router.assign_request",
        call_trace=False,
        activate=True,
        request_meta=request_meta,
        distributed_context=context,
        is_streaming=getattr(request_meta, "is_streaming", None),
        handle_source=getattr(getattr(instance, "_handle_source", None), "value", None),
    ) as ctx:
        core.dispatch("ray.serve.request.metadata.inject", (ctx, request_meta))

        return await func(*args, **kwargs)


def traced_deployment_handle_remote(func, instance: "DeploymentHandle", args, kwargs):
    deployment_id = getattr(instance, "deployment_id", None)
    with core.context_with_data(
        "ray.deployment.remote",
        span_name="deployment.remote",
        deployment_name=instance.deployment_name,
        app_name=getattr(deployment_id, "app_name", None),
        deployment_args=args,
        deployment_kwargs=kwargs,
    ) as ctx:
        response = func(*args, **kwargs)

        request_meta: RequestMetadata = response._request_metadata
        core.dispatch("ray.serve.request.metadata.inject", (ctx, request_meta))

        return response


async def traced_handle_request_with_rejection(func, instance: "ReplicaBase", args, kwargs):
    request_meta: RequestMetadata = cast(RequestMetadata, get_argument_value(args, kwargs, 0, "request_metadata"))
    context = ServeRequestContextPropagator.extract_from_request_metadata(request_meta)

    with core.context_with_data(
        "ray.handle.request.with.rejection",
        span_name="handle_request_with_rejection",
        call_trace=False,
        activate=True,
        request_meta=request_meta,
        distributed_context=context,
        replica_id=instance._replica_id.to_full_id_str(),
    ) as ctx:
        core.dispatch("ray.serve.request.metadata.inject", (ctx, request_meta))

        async_generator = func(*args, **kwargs)
        async for value in async_generator:
            accepted = getattr(value, "accepted", None)
            if isinstance(accepted, bool):
                core.set_item("ray_serve_request_rejected", not accepted)

            yield value


async def traced_proxy_request(func, instance, args, kwargs):
    proxy_request: ProxyRequest = cast(ProxyRequest, get_argument_value(args, kwargs, 0, "proxy_request"))
    grpc_context = None

    if isinstance(proxy_request, gRPCProxyRequest):
        grpc_context = proxy_request.ray_serve_grpc_context
        resource = proxy_request.service_method
        distributed_context = extract_grpc_context(grpc_context)
    else:
        method = proxy_request.method
        route_path: Optional[str] = None
        distributed_context = _extract_proxy_request_http_context(proxy_request)

        # Use Ray Serve's matched route pattern (e.g. /model2/{model_name}) when
        # available, mirroring what Ray logs/metrics use, to avoid high-cardinality
        # resources from concrete URL paths (e.g. /model2/my_model).
        try:
            route_path = _get_proxy_request_route_pattern(instance, proxy_request) or route_path
        except Exception:
            log.debug("Could not determine matched route pattern for proxy request", exc_info=True)
            # Best-effort only: keep current behavior if route pattern matching fails.
            pass

        resource = f"{method} {route_path}"

    with core.context_with_data(
        "ray.proxy.request",
        span_name="proxy_request",
        resource=resource,
        activate=True,
        call_trace=False,
        distributed_context=tracer.current_trace_context() if distributed_context is None else distributed_context,
        proxy_request=proxy_request,
    ) as ctx:
        if grpc_context is not None:
            core.dispatch("ray.serve.grpc.context.inject", (ctx, grpc_context))

        async_generator = func(*args, **kwargs)
        async for value in async_generator:
            if isinstance(value, ResponseStatus):
                core.set_item("response_status", value)
            yield value


def _trace_deployment_method(method, deployment_name, is_ingress_call: bool = False):
    # Prevents from tracing twice a function
    if getattr(method, _DD_RAY_SERVE_WRAPPED_ATTR, False):
        return method

    # When calling Deployment.remote(), by default the function call is __call__
    # We rename it to invoke to make it clearer.
    #
    # Additionnally, if the deployment is a function, we want to prevent to have
    # MyDeployment.MyDeployment as a resource_name
    method_name = method.__name__
    resource_name = "invoke" if method_name in ("__call__", deployment_name) else method_name

    if inspect.iscoroutinefunction(method):

        @wraps(method)
        async def _traced_async(*args, **kwargs):
            with core.context_with_data(
                "ray.serve.deployment",
                span_name="deployment.method_execution",
                resource=f"ServeDeployment:{deployment_name}.{resource_name}",
            ) as ctx:
                _set_deployment_method_span_metadata(ctx, deployment_name, resource_name)
                result = await method(*args, **kwargs)
                # Fast path: only ingress-wrapper __call__ can map invoke -> endpoint method.
                if is_ingress_call and resource_name == "invoke":
                    scope = get_argument_value(args, kwargs, 1, "scope")
                    if endpoint_name := _get_ingress_endpoint_method_name(scope):
                        core.dispatch("ray.serve.deployment.resource.set", (ctx, deployment_name, endpoint_name))
                return result

        wrapped_method = _traced_async
    else:

        @wraps(method)
        def _traced_sync(*args, **kwargs):
            with core.context_with_data(
                "ray.serve.deployment",
                span_name="deployment.method_execution",
                resource=f"ServeDeployment:{deployment_name}.{resource_name}",
            ) as ctx:
                _set_deployment_method_span_metadata(ctx, deployment_name, resource_name)
                return method(*args, **kwargs)

        wrapped_method = _traced_sync

    setattr(wrapped_method, _DD_RAY_SERVE_WRAPPED_ATTR, True)
    return wrapped_method


def _instrument_serve_deployment_class(cls, deployment_name):
    """Instrument a Ray Serve deployment class in-place.

    For regular deployments, we wrap methods defined directly on ``cls``.
    For ``@serve.ingress`` deployments, Ray builds a wrapper class that
    inherits user methods and routes requests through ``ASGIAppReplicaWrapper``.
    In that case we also walk base classes so inherited user methods are traced,
    while avoiding wrapper internals and duplicate overrides.
    """

    def _is_serve_ingress_wrapper(cls) -> bool:
        return cls.__name__ != "ASGIAppReplicaWrapper" and any(
            base.__name__ == "ASGIAppReplicaWrapper" for base in cls.__mro__
        )

    is_ingress_wrapper = _is_serve_ingress_wrapper(cls)

    # In ingress mode, inspect bases to catch inherited user handlers.
    # Otherwise, only instrument methods declared on the class itself.
    classes = cls.__mro__[1:] if is_ingress_wrapper else (cls,)
    for base in classes:
        if base is object:
            continue
        is_asgi_wrapper = base.__name__ == "ASGIAppReplicaWrapper"

        for name, attribute in base.__dict__.items():
            # Do not overwrite attributes already defined on the wrapper class.
            if is_ingress_wrapper and name in cls.__dict__:
                continue
            if name.startswith("__") and name != "__call__":
                continue
            # From the ASGI wrapper, only __call__ is relevant as request entrypoint.
            if is_asgi_wrapper and name != "__call__":
                continue

            # Mark only the ingress wrapper __call__ path specially so downstream
            # tracing can classify request-level spans.
            is_ingress_call = is_ingress_wrapper and is_asgi_wrapper and name == "__call__"
            if isinstance(attribute, staticmethod):
                traced_attribute = staticmethod(
                    _trace_deployment_method(attribute.__func__, deployment_name, is_ingress_call)
                )
            elif isinstance(attribute, classmethod):
                traced_attribute = classmethod(
                    _trace_deployment_method(attribute.__func__, deployment_name, is_ingress_call)
                )
            elif inspect.isfunction(attribute):
                traced_attribute = _trace_deployment_method(attribute, deployment_name, is_ingress_call)
            else:
                continue

            setattr(cls, name, traced_attribute)

    return cls


def _instrument_serve_deployment(func_or_class, deployment_name):
    if inspect.isclass(func_or_class):
        return _instrument_serve_deployment_class(func_or_class, deployment_name)
    if callable(func_or_class):
        return _trace_deployment_method(func_or_class, deployment_name)
    return func_or_class


def traced_serve_deployment(func, instance, args, kwargs):
    """Serve deployment can be created using two ways:
    - passing the deployment as an arg/kwarg to serve.deployment
    - by annotating a class with @serve.deployment
    """
    func_or_class = get_argument_value(args, kwargs, 0, "_func_or_class", True)
    deployment_name = get_argument_value(args, kwargs, 1, "name", True)
    if deployment_name is None and func_or_class is not None:
        deployment_name = func_or_class.__name__

    # When deployment is passed as an argument to serve.deployment
    if func_or_class is not None:
        instrumented_target = _instrument_serve_deployment(func_or_class, deployment_name)
        # Preserve the original calling convention when replacing the target.
        if "_func_or_class" in kwargs:
            kwargs = dict(kwargs)
            kwargs["_func_or_class"] = instrumented_target
            return func(*args, **kwargs)
        return func(instrumented_target, *args[1:], **kwargs)

    # When the deployment is annotated by @serve.deployment
    decorator = func(*args, **kwargs)

    @wraps(decorator)
    def _traced_decorator(func_or_class):
        return decorator(_instrument_serve_deployment(func_or_class, deployment_name))

    return _traced_decorator
