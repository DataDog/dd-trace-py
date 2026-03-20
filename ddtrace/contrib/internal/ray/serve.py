import inspect
from functools import wraps
from typing import Any
from typing import Dict
from typing import TYPE_CHECKING
from typing import cast

from ray.serve._private.common import RequestMetadata
from ray.serve._private.proxy_request_response import ProxyRequest
from ray.serve._private.proxy_request_response import gRPCProxyRequest

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import Context
from ddtrace.trace import tracer


if TYPE_CHECKING:
    from ray.actor import ActorMethod
    from ray.serve._private.replica import ReplicaBase
    from ray.serve.grpc_util import RayServegRPCContext
    from ray.serve.handle import DeploymentHandle

log = get_logger(__name__)
_DD_REQUEST_METADATA_TRACE_CONTEXT_ATTR = "_dd_trace_context_headers"
_DD_RAY_SERVE_WRAPPED_ATTR = "__dd_ray_serve_wrapped__"


class ContextPropagator:
    @staticmethod
    def extract(headers: "RayServegRPCContext | None") -> Context:
        # `headers` is None when calling the deployment handle remote, which is used in
        # the integration tests to reconfigure() (used in the throttling tests)
        metadata = dict(headers.invocation_metadata()) if headers is not None else {}
        return HTTPPropagator.extract(metadata)

    @staticmethod
    def inject(span_context: Context, headers: "RayServegRPCContext | None") -> None:
        # `headers` is None when calling the deployment handle remote, which is used in
        # the integration tests to reconfigure() (used in the throttling tests)
        if headers is None:
            return
        invocation_metadata = dict(headers.invocation_metadata())
        HTTPPropagator.inject(span_context, invocation_metadata)
        headers._invocation_metadata = list(invocation_metadata.items())


def get_request_metadata(args: tuple) -> RequestMetadata | None:
    for arg in args:
        if isinstance(arg, RequestMetadata):
            return arg
    log.warning("No RequestMetadata found in arguments, will not extract trace context.")
    return None


def maybe_activate_trace_context(args: tuple) -> Context | None:
    """Activates the trace context if there is no parent context to inherit from."""
    if tracer.current_trace_context() is None and (request_metadata := get_request_metadata(args)) is not None:
        context_headers = get_context_headers(request_metadata)
        if context_headers:
            context = HTTPPropagator.extract(context_headers)
        else:
            context = ContextPropagator.extract(request_metadata.grpc_context)
        tracer.context_provider.activate(context)
        return context
    return None


def set_context_headers(request_metadata: RequestMetadata, headers: Dict[str, str]) -> None:
    setattr(request_metadata, _DD_REQUEST_METADATA_TRACE_CONTEXT_ATTR, headers)


def get_context_headers(request_metadata: RequestMetadata) -> Dict[str, str] | None:
    headers = cast(Any, getattr(request_metadata, _DD_REQUEST_METADATA_TRACE_CONTEXT_ATTR, None))
    if isinstance(headers, dict):
        return headers
    return None


async def traced_handle_request_with_rejection(func, instance: "ReplicaBase", args, kwargs):
    maybe_activate_trace_context(args)
    with tracer.trace("ray.handle_request_with_rejection") as span:
        if (request_metadata := get_request_metadata(args)) is not None:
            ContextPropagator.inject(span.context, request_metadata.grpc_context)
            span.set_tag("ray.request_id", request_metadata.request_id)
        span.set_tag("ray.replica_id", instance._replica_id.to_full_id_str())
        span.set_tag("ray.component_name", instance._component_name)

        async_generator = func(*args, **kwargs)
        async for value in async_generator:
            yield value


async def traced_shutdown(func, instance: "ReplicaBase", args, kwargs):
    with tracer.trace("ray.shutdown") as span:
        span.set_tag("ray.replica_id", instance._replica_id.to_full_id_str())
        span.set_tag("ray.component_name", instance._component_name)
        return await func(*args, **kwargs)


def traced_actor_remote(func, instance: "ActorMethod", args, kwargs):
    if any(p.name == "kwargs" for p in (instance._signature or ())):
        kwargs["context"] = tracer.current_trace_context()
    with tracer.trace(f"ray.actor.remote.{instance._method_name}") as span:
        span.set_tag("ray.actor_method", instance._method_name)
        return func(*args, **kwargs)


def traced_deployment_handle_remote(func, instance: "DeploymentHandle", args, kwargs):
    with tracer.trace("ray.deployment_handle.remote") as span:
        span.set_tag("ray.deployment_id", instance.deployment_id.to_replica_actor_class_name())
        span.set_tag("ray.handle_id", instance.handle_id)
        return func(*args, **kwargs)


async def traced_proxy_request(func, instance, args, kwargs):
    proxy_request = cast(ProxyRequest, kwargs.get("proxy_request", None) or args[-1])
    if isinstance(proxy_request, gRPCProxyRequest):
        grpc_context = proxy_request.ray_serve_grpc_context
        method = proxy_request.service_method
        if tracer.current_trace_context() is None:
            tracer.context_provider.activate(ContextPropagator.extract(grpc_context))
    else:
        grpc_context = None
        method = proxy_request.method
    with tracer.trace(f"ray.proxy_request.{method}") as span:
        if grpc_context is not None:
            ContextPropagator.inject(span.context, grpc_context)
        async_generator = func(*args, **kwargs)
        async for value in async_generator:
            yield value


async def traced_assign_request(func, instance, args, kwargs):
    maybe_activate_trace_context(args)
    with tracer.trace("ray.assign_request") as span:
        if (request_metadata := get_request_metadata(args)) is not None:
            if request_metadata.is_http_request:
                headers = {}
                HTTPPropagator.inject(span.context, headers)
                set_context_headers(request_metadata, headers)
            else:
                ContextPropagator.inject(span.context, request_metadata.grpc_context)
            span.set_tag("ray.request_id", request_metadata.request_id)
        return await func(*args, **kwargs)


def _trace_serve_method(method):
    if getattr(method, _DD_RAY_SERVE_WRAPPED_ATTR, False):
        return method

    if inspect.iscoroutinefunction(method):
        @wraps(method)
        async def _traced_async(*args, **kwargs):
            from ddtrace.trace import tracer as _tracer

            with _tracer.trace(f"ray.serve.deployment.{method.__name__}"):
                return await method(*args, **kwargs)
        wrapped_method = _traced_async
    else:
        @wraps(method)
        def _traced_sync(*args, **kwargs):
            from ddtrace.trace import tracer as _tracer

            with _tracer.trace(f"ray.serve.deployment.{method.__name__}"):
                return method(*args, **kwargs)
        wrapped_method = _traced_sync

    setattr(wrapped_method, _DD_RAY_SERVE_WRAPPED_ATTR, True)
    return wrapped_method


def _instrument_serve_deployment_class(cls):
    for name, attribute in cls.__dict__.items():
        if name.startswith("__"):
            continue

        if isinstance(attribute, staticmethod):
            setattr(cls, name, staticmethod(_trace_serve_method(attribute.__func__)))
        elif isinstance(attribute, classmethod):
            setattr(cls, name, classmethod(_trace_serve_method(attribute.__func__)))
        elif inspect.isfunction(attribute):
            setattr(cls, name, _trace_serve_method(attribute))

    return cls


def _instrument_serve_deployment(func_or_class):
    if inspect.isclass(func_or_class):
        return _instrument_serve_deployment_class(func_or_class)
    if callable(func_or_class):
        return _trace_serve_method(func_or_class)
    return func_or_class


def traced_serve_deployment(func, instance, args, kwargs):
    """Serve deployment can be created using two ways:
        - passing the deployment as an arg/kwarg to serve.deployment
        - by annotating a class with @serve.deployment
    """
    # Checking if the deployment is passed as an argument
    func_or_class = get_argument_value(args, kwargs, 0, "_func_or_class", True)

    if func_or_class is not None:
        instrumented_target = _instrument_serve_deployment(func_or_class)
        # Preserve the original calling convention when replacing the target.
        if "_func_or_class" in kwargs:
            kwargs = dict(kwargs)
            kwargs["_func_or_class"] = instrumented_target
            return func(*args, **kwargs)
        return func(instrumented_target, *args[1:], **kwargs)

    # Decorator form is used. We return a decorator
    decorator = func(*args, **kwargs)

    @wraps(decorator)
    def _traced_decorator(func_or_class):
        return decorator(_instrument_serve_deployment(func_or_class))

    return _traced_decorator
