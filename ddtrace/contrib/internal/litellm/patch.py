import os
import sys
from importlib.metadata import version

import litellm

from ddtrace import config
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.contrib.internal.litellm.utils import tag_request
from ddtrace.contrib.internal.litellm.utils import tag_model_and_provider
from ddtrace.llmobs._integrations import LiteLLMIntegration
from ddtrace.trace import Pin
from ddtrace.internal.utils import get_argument_value


config._add(
    "litellm",
    {},
)


def get_version():
    # type: () -> str
    try:
        return version("litellm")
    except Exception:
        return ""


def _create_span(litellm, pin, func, instance, args, kwargs):
    """Helper function to create and configure a traced span."""
    integration = litellm._datadog_integration
    span = integration.trace(
        pin,
        "litellm.%s" % func.__name__,
        submit_to_llmobs=False,
    )
    return span


@with_traced_module
def traced_completion(litellm, pin, func, instance, args, kwargs):
    requested_model = get_argument_value(args, kwargs, 0, "model", None)
    span = _create_span(litellm, pin, func, instance, args, kwargs)
    tag_request(span, kwargs)
    try:
        return func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        # tag model and provider
        tag_model_and_provider(litellm, span, requested_model)
        span.finish()


@with_traced_module
async def traced_acompletion(litellm, pin, func, instance, args, kwargs):
    requested_model = get_argument_value(args, kwargs, 0, "model", None)
    span = _create_span(litellm, pin, func, instance, args, kwargs)
    tag_request(span, kwargs)
    try:
        return await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        # tag model and provider
        tag_model_and_provider(litellm, span, requested_model)
        span.finish()


@with_traced_module
def traced_get_llm_provider(litellm, pin, func, instance, args, kwargs):
    requested_model = get_argument_value(args, kwargs, 0, "model", None)
    integration = litellm._datadog_integration
    model, custom_llm_provider, dynamic_api_key, api_base = func(*args, **kwargs)
    # Store the provider information in the integration
    integration._provider_map[requested_model] = custom_llm_provider
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


def unpatch():
    if not getattr(litellm, "_datadog_patch", False):
        return

    litellm._datadog_patch = False

    unwrap(litellm, "completion")
    unwrap(litellm, "acompletion")
    unwrap(litellm, "text_completion")
    unwrap(litellm, "atext_completion")
    unwrap(litellm, "get_llm_provider")

    delattr(litellm, "_datadog_integration")