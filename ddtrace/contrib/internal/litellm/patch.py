import os
import sys

import litellm

from ddtrace import config
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.contrib.internal.litellm.utils import get_provider
from ddtrace.llmobs._integrations import LiteLLMIntegration
from ddtrace.trace import Pin
from ddtrace.internal.utils import get_argument_value


config._add(
    "litellm",
    {
        "span_prompt_completion_sample_rate": float(os.getenv("DD_LITELLM_SPAN_PROMPT_COMPLETION_SAMPLE_RATE", 1.0)),
        "span_char_limit": int(os.getenv("DD_LITELLM_SPAN_CHAR_LIMIT", 128)),
    },
)


def get_version():
    # type: () -> str
    return getattr(litellm, "__version__", "")


def _create_span(litellm, pin, func, instance, args, kwargs):
    """Helper function to create and configure a traced span."""
    integration = litellm._datadog_integration
    model = get_argument_value(args, kwargs, 0, "model", None)
    span = integration.trace(
        pin,
        "litellm.%s" % func.__name__,
        model=model,
        provider=get_provider(model),
        submit_to_llmobs=False,
    )
    return span


@with_traced_module
def traced_completion(litellm, pin, func, instance, args, kwargs):
    span = _create_span(litellm, pin, func, instance, args, kwargs)
    try:
        return func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        span.finish()


@with_traced_module
async def traced_acompletion(litellm, pin, func, instance, args, kwargs):
    span = _create_span(litellm, pin, func, instance, args, kwargs)
    try:
        return await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        span.finish()


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


def unpatch():
    if not getattr(litellm, "_datadog_patch", False):
        return

    litellm._datadog_patch = False

    unwrap("litellm", "completion", traced_completion(litellm))
    unwrap("litellm", "acompletion", traced_acompletion(litellm))
    unwrap("litellm", "text_completion", traced_completion(litellm))
    unwrap("litellm", "atext_completion", traced_acompletion(litellm))

    delattr(litellm, "_datadog_integration")