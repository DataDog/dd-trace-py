from typing import Dict

import litellm

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.events.llm import LlmRequestEvent
from ddtrace.contrib.internal.litellm.utils import LiteLLMAsyncStreamHandler
from ddtrace.contrib.internal.litellm.utils import LiteLLMStreamHandler
from ddtrace.contrib.internal.litellm.utils import extract_host_tag
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import LITELLM_ROUTER_INSTANCE_KEY
from ddtrace.llmobs._integrations import LiteLLMIntegration
from ddtrace.llmobs._integrations.base_stream_handler import make_traced_stream


config._add("litellm", {})


def get_version() -> str:
    version_module = getattr(litellm, "_version", None)
    return getattr(version_module, "version", "")


def _supported_versions() -> Dict[str, str]:
    return {"litellm": "*"}


def _create_llm_event(pin, integration, operation, kwargs, model, host, submit_to_llmobs=True):
    """Create an LlmRequestEvent for a LiteLLM call."""
    base_url = kwargs.get("base_url", None) or kwargs.get("api_base", None)
    return LlmRequestEvent(
        service=int_service(pin, integration.integration_config),
        resource=operation,
        integration_name="litellm",
        provider="",
        model=model or "",
        integration=integration,
        submit_to_llmobs=submit_to_llmobs,
        request_kwargs=kwargs,
        base_tag_kwargs={
            "model": model,
            "host": host,
            "base_url": base_url,
        },
        measured=True,
    )


def _handle_router_stream_response(resp, span, kwargs, instance, integration, args, is_async=False):
    """
    Handle router streaming responses with fallback for different wrapper types.

    In litellm>=1.74.15, router streaming responses may be wrapped in FallbackStreamWrapper
    (for mid-stream fallback support) or other types that don't expose the .handler attribute.
    """
    if hasattr(resp, "handler") and hasattr(resp.handler, "add_span"):
        resp.handler.add_span(span, kwargs, instance)
        return resp

    # Fallback: wrap the response in our own traced stream for compatibility
    kwargs[LITELLM_ROUTER_INSTANCE_KEY] = instance
    handler_class = LiteLLMAsyncStreamHandler if is_async else LiteLLMStreamHandler
    return make_traced_stream(resp, handler_class(integration, span, args, kwargs))


@with_traced_module
def traced_completion(litellm, pin, func, instance, args, kwargs):
    operation = func.__name__
    integration = litellm._datadog_integration
    model = get_argument_value(args, kwargs, 0, "model", None)
    host = extract_host_tag(kwargs)
    event = _create_llm_event(
        pin,
        integration,
        operation,
        kwargs,
        model,
        host,
        submit_to_llmobs=not integration._has_downstream_openai_span(kwargs, model),
    )
    stream = kwargs.get("stream", False)

    with core.context_with_event(event) as ctx:
        resp = None
        try:
            resp = func(*args, **kwargs)
            if stream:
                ctx.set_item("is_stream", True)
                event._end_span = False
                return make_traced_stream(resp, LiteLLMStreamHandler(integration, ctx.span, args, kwargs))
        finally:
            if not ctx.get_item("is_stream", False):
                ctx.set_item("response", resp)
                ctx.set_item("operation", operation)
                ctx.set_item("llmobs_args", args)
    return resp


@with_traced_module
async def traced_acompletion(litellm, pin, func, instance, args, kwargs):
    operation = func.__name__
    integration = litellm._datadog_integration
    model = get_argument_value(args, kwargs, 0, "model", None)
    host = extract_host_tag(kwargs)
    event = _create_llm_event(
        pin,
        integration,
        operation,
        kwargs,
        model,
        host,
        submit_to_llmobs=not integration._has_downstream_openai_span(kwargs, model),
    )
    stream = kwargs.get("stream", False)

    with core.context_with_event(event) as ctx:
        resp = None
        try:
            resp = await func(*args, **kwargs)
            if stream:
                ctx.set_item("is_stream", True)
                event._end_span = False
                return make_traced_stream(resp, LiteLLMAsyncStreamHandler(integration, ctx.span, args, kwargs))
        finally:
            if not ctx.get_item("is_stream", False):
                ctx.set_item("response", resp)
                ctx.set_item("operation", operation)
                ctx.set_item("llmobs_args", args)
    return resp


@with_traced_module
def traced_router_completion(litellm, pin, func, instance, args, kwargs):
    operation = f"router.{func.__name__}"
    integration = litellm._datadog_integration
    model = get_argument_value(args, kwargs, 0, "model", None)
    host = extract_host_tag(kwargs)
    event = _create_llm_event(pin, integration, operation, kwargs, model, host)
    stream = kwargs.get("stream", False)

    with core.context_with_event(event) as ctx:
        resp = None
        try:
            resp = func(*args, **kwargs)
            if stream:
                ctx.set_item("is_stream", True)
                event._end_span = False
                return _handle_router_stream_response(
                    resp, ctx.span, kwargs, instance, integration, args, is_async=False
                )
        finally:
            if not ctx.get_item("is_stream", False):
                kwargs[LITELLM_ROUTER_INSTANCE_KEY] = instance
                ctx.set_item("response", resp)
                ctx.set_item("operation", operation)
                ctx.set_item("llmobs_args", args)
    return resp


@with_traced_module
async def traced_router_acompletion(litellm, pin, func, instance, args, kwargs):
    operation = f"router.{func.__name__}"
    integration = litellm._datadog_integration
    model = get_argument_value(args, kwargs, 0, "model", None)
    host = extract_host_tag(kwargs)
    event = _create_llm_event(pin, integration, operation, kwargs, model, host)
    stream = kwargs.get("stream", False)

    with core.context_with_event(event) as ctx:
        resp = None
        try:
            resp = await func(*args, **kwargs)
            if stream:
                ctx.set_item("is_stream", True)
                event._end_span = False
                return _handle_router_stream_response(
                    resp, ctx.span, kwargs, instance, integration, args, is_async=True
                )
        finally:
            if not ctx.get_item("is_stream", False):
                kwargs[LITELLM_ROUTER_INSTANCE_KEY] = instance
                ctx.set_item("response", resp)
                ctx.set_item("operation", operation)
                ctx.set_item("llmobs_args", args)
    return resp


@with_traced_module
def traced_get_llm_provider(litellm, pin, func, instance, args, kwargs):
    requested_model = get_argument_value(args, kwargs, 0, "model", None)
    integration = litellm._datadog_integration
    model, custom_llm_provider, dynamic_api_key, api_base = func(*args, **kwargs)
    # store the model name and provider in the integration
    integration._model_map[requested_model] = (model, custom_llm_provider)
    return model, custom_llm_provider, dynamic_api_key, api_base


def patch():
    if getattr(litellm, "_datadog_patch", False):
        return

    litellm._datadog_patch = True

    Pin().onto(litellm)
    integration = LiteLLMIntegration(integration_config=config.litellm)
    litellm._datadog_integration = integration

    wrap("litellm", "completion", traced_completion(litellm))
    wrap("litellm", "acompletion", traced_acompletion(litellm))
    wrap("litellm", "text_completion", traced_completion(litellm))
    wrap("litellm", "atext_completion", traced_acompletion(litellm))
    wrap("litellm", "get_llm_provider", traced_get_llm_provider(litellm))
    wrap("litellm", "main.get_llm_provider", traced_get_llm_provider(litellm))
    wrap("litellm", "router.Router.completion", traced_router_completion(litellm))
    wrap("litellm", "router.Router.acompletion", traced_router_acompletion(litellm))
    wrap("litellm", "router.Router.text_completion", traced_router_completion(litellm))
    wrap("litellm", "router.Router.atext_completion", traced_router_acompletion(litellm))


def unpatch():
    if not getattr(litellm, "_datadog_patch", False):
        return

    litellm._datadog_patch = False

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
    delattr(litellm, "_datadog_integration")
