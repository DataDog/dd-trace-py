import sys

import litellm

from ddtrace import config
from ddtrace.contrib.internal.litellm.utils import TracedLiteLLMAsyncStream
from ddtrace.contrib.internal.litellm.utils import TracedLiteLLMStream
from ddtrace.contrib.internal.litellm.utils import extract_host_tag
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import LITELLM_ROUTER_INSTANCE_KEY
from ddtrace.llmobs._integrations import LiteLLMIntegration
from ddtrace.trace import Pin


config._add("litellm", {})


def get_version() -> str:
    version_module = getattr(litellm, "_version", None)
    return getattr(version_module, "version", "")


@with_traced_module
def traced_completion(litellm, pin, func, instance, args, kwargs):
    operation = func.__name__
    integration = litellm._datadog_integration
    model = get_argument_value(args, kwargs, 0, "model", None)
    host = extract_host_tag(kwargs)
    span = integration.trace(
        pin,
        operation,
        model=model,
        host=host,
        submit_to_llmobs=integration.should_submit_to_llmobs(kwargs, model),
    )
    stream = kwargs.get("stream", False)
    resp = None
    try:
        resp = func(*args, **kwargs)
        if stream:
            return TracedLiteLLMStream(resp, integration, span, kwargs)
        return resp
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        # streamed spans will be finished separately once the stream generator is exhausted
        if not stream:
            if integration.is_pc_sampled_llmobs(span):
                integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=resp, operation=operation)
            span.finish()


@with_traced_module
async def traced_acompletion(litellm, pin, func, instance, args, kwargs):
    operation = func.__name__
    integration = litellm._datadog_integration
    model = get_argument_value(args, kwargs, 0, "model", None)
    host = extract_host_tag(kwargs)
    span = integration.trace(
        pin,
        operation,
        model=model,
        host=host,
        submit_to_llmobs=integration.should_submit_to_llmobs(kwargs, model),
    )
    stream = kwargs.get("stream", False)
    resp = None
    try:
        resp = await func(*args, **kwargs)
        if stream:
            return TracedLiteLLMAsyncStream(resp, integration, span, kwargs)
        return resp
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        # streamed spans will be finished separately once the stream generator is exhausted
        if not stream:
            if integration.is_pc_sampled_llmobs(span):
                integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=resp, operation=operation)
            span.finish()


@with_traced_module
def traced_router_completion(litellm, pin, func, instance, args, kwargs):
    operation = f"router.{func.__name__}"
    integration = litellm._datadog_integration
    model = get_argument_value(args, kwargs, 0, "model", None)
    host = extract_host_tag(kwargs)
    span = integration.trace(
        pin,
        operation,
        model=model,
        host=host,
        submit_to_llmobs=True,
    )
    stream = kwargs.get("stream", False)
    resp = None
    try:
        resp = func(*args, **kwargs)
        if stream:
            resp._add_router_span_info(span, kwargs, instance)
        return resp
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        if not stream:
            if integration.is_pc_sampled_llmobs(span):
                kwargs[LITELLM_ROUTER_INSTANCE_KEY] = instance
                integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=resp, operation=operation)
            span.finish()


@with_traced_module
async def traced_router_acompletion(litellm, pin, func, instance, args, kwargs):
    operation = f"router.{func.__name__}"
    integration = litellm._datadog_integration
    model = get_argument_value(args, kwargs, 0, "model", None)
    host = extract_host_tag(kwargs)
    span = integration.trace(
        pin,
        operation,
        model=model,
        host=host,
        submit_to_llmobs=True,
    )
    stream = kwargs.get("stream", False)
    resp = None
    try:
        resp = await func(*args, **kwargs)
        if stream:
            resp._add_router_span_info(span, kwargs, instance)
        return resp
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        if not stream:
            if integration.is_pc_sampled_llmobs(span):
                kwargs[LITELLM_ROUTER_INSTANCE_KEY] = instance
                integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=resp, operation=operation)
            span.finish()


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
