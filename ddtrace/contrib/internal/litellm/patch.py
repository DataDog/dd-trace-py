import sys

import litellm

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.litellm._constants import COMPONENT
from ddtrace.contrib.internal.litellm._constants import ROUTER_INSTANCE_KEY
from ddtrace.contrib.internal.litellm._constants import TAG_HOST
from ddtrace.contrib.internal.litellm._constants import TAG_MODEL
from ddtrace.contrib.internal.litellm.utils import BaseLiteLLMStreamHandler
from ddtrace.contrib.internal.litellm.utils import LiteLLMAsyncStreamHandler
from ddtrace.contrib.internal.litellm.utils import LiteLLMStreamHandler
from ddtrace.contrib.internal.litellm.utils import extract_host_tag
from ddtrace.contrib.internal.litellm.utils import has_downstream_openai_span
from ddtrace.contrib.internal.litellm.utils import model_map
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal import integration_hooks
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.streaming import make_traced_stream


config._add("litellm", {})


def get_version() -> str:
    version_module = getattr(litellm, "_version", None)
    return getattr(version_module, "version", "")


def _supported_versions() -> dict[str, str]:
    return {"litellm": "*"}


def _request_event(operation, args, kwargs, router=None):
    model = get_argument_value(args, kwargs, 0, "model", None)
    host = extract_host_tag(kwargs)
    tags = {}
    if model is not None:
        tags[TAG_MODEL] = model
    if host is not None:
        tags[TAG_HOST] = host
    # Copy so the router instance never reaches the wrapped call.
    request_kwargs = dict(kwargs)
    if router is not None:
        request_kwargs[ROUTER_INSTANCE_KEY] = router
    return LlmRequestEvent(
        component=COMPONENT,
        integration_config=config.litellm,
        service=int_service(None, config.litellm),
        resource=operation,
        # The provider is only known once litellm resolves the model.
        provider="",
        model=model,
        tags=tags,
        # Router spans are always submitted; plain calls defer to the OpenAI integration's span when there is one.
        submit_to_llmobs=router is not None or not has_downstream_openai_span(kwargs, model),
        request_args=args,
        request_kwargs=request_kwargs,
        operation=operation,
    )


def _handle_router_stream_response(resp, ctx, is_async=False):
    """
    Handle router streaming responses with fallback for different wrapper types.

    In litellm>=1.74.15, router streaming responses may be wrapped in FallbackStreamWrapper
    (for mid-stream fallback support) or other types that don't expose the .handler attribute.
    """
    handler = getattr(resp, "handler", None)
    if isinstance(handler, BaseLiteLLMStreamHandler):
        handler.add_context(ctx)
        return resp

    # Fallback: wrap the response in our own traced stream for compatibility
    handler_class = LiteLLMAsyncStreamHandler if is_async else LiteLLMStreamHandler
    return make_traced_stream(resp, handler_class(ctx))


def traced_completion(func, instance, args, kwargs):
    event = _request_event(func.__name__, args, kwargs)
    if not kwargs.get("stream", False):
        with core.context_with_event(event):
            event.response = func(*args, **kwargs)
            return event.response
    # The stream handler dispatches the ended event once the stream is consumed or closed.
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            resp = func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        return make_traced_stream(resp, LiteLLMStreamHandler(ctx))


async def traced_acompletion(func, instance, args, kwargs):
    event = _request_event(func.__name__, args, kwargs)
    if not kwargs.get("stream", False):
        with core.context_with_event(event):
            event.response = await func(*args, **kwargs)
            return event.response
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            resp = await func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        return make_traced_stream(resp, LiteLLMAsyncStreamHandler(ctx))


def traced_router_completion(func, instance, args, kwargs):
    event = _request_event(f"router.{func.__name__}", args, kwargs, router=instance)
    if not kwargs.get("stream", False):
        with core.context_with_event(event):
            event.response = func(*args, **kwargs)
            return event.response
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            resp = func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        return _handle_router_stream_response(resp, ctx, is_async=False)


async def traced_router_acompletion(func, instance, args, kwargs):
    event = _request_event(f"router.{func.__name__}", args, kwargs, router=instance)
    if not kwargs.get("stream", False):
        with core.context_with_event(event):
            event.response = await func(*args, **kwargs)
            return event.response
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            resp = await func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        return _handle_router_stream_response(resp, ctx, is_async=True)


def traced_get_llm_provider(func, instance, args, kwargs):
    requested_model = get_argument_value(args, kwargs, 0, "model", None)
    model, custom_llm_provider, dynamic_api_key, api_base = func(*args, **kwargs)
    model_map[requested_model] = (model, custom_llm_provider)
    return model, custom_llm_provider, dynamic_api_key, api_base


def patch():
    if getattr(litellm, "_datadog_patch", False):
        return

    litellm._datadog_patch = True

    wrap("litellm", "completion", traced_completion)
    wrap("litellm", "acompletion", traced_acompletion)
    wrap("litellm", "text_completion", traced_completion)
    wrap("litellm", "atext_completion", traced_acompletion)
    wrap("litellm", "get_llm_provider", traced_get_llm_provider)
    wrap("litellm", "main.get_llm_provider", traced_get_llm_provider)
    wrap("litellm", "router.Router.completion", traced_router_completion)
    wrap("litellm", "router.Router.acompletion", traced_router_acompletion)
    wrap("litellm", "router.Router.text_completion", traced_router_completion)
    wrap("litellm", "router.Router.atext_completion", traced_router_acompletion)

    # Let products (LLMObs) attach their own LlmEvents subscribers without this
    # module importing them.
    integration_hooks.on_patch(COMPONENT)


def unpatch():
    if not getattr(litellm, "_datadog_patch", False):
        return

    litellm._datadog_patch = False

    integration_hooks.on_unpatch(COMPONENT)

    unwrap(litellm, "completion")
    unwrap(litellm, "acompletion")
    unwrap(litellm, "text_completion")
    unwrap(litellm, "atext_completion")
    unwrap(litellm, "get_llm_provider")
    unwrap(litellm.main, "get_llm_provider")
    unwrap(litellm.router.Router, "completion")
    unwrap(litellm.router.Router, "acompletion")
    unwrap(litellm.router.Router, "text_completion")
    unwrap(litellm.router.Router, "atext_completion")
