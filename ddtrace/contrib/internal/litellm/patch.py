import sys

import litellm

from ddtrace import config
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations import LiteLLMIntegration
from ddtrace.trace import Pin


config._add("litellm", {})


def get_version() -> str:
    version_module = getattr(litellm, "_version", None)
    return getattr(version_module, "version", "") if version_module else ""


@with_traced_module
def traced_completion(litellm, pin, func, instance, args, kwargs):
    integration = litellm._datadog_integration
    model = get_argument_value(args, kwargs, 0, "model", None)
    host = None
    if "host" in kwargs.get("metadata", {}).get("headers", {}):
        host = kwargs["metadata"]["headers"]["host"]
    span = integration.trace(
        pin,
        func.__name__,
        model=model,
        host=host,
        submit_to_llmobs=False,
    )
    try:
        return func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        span.finish()


@with_traced_module
async def traced_acompletion(litellm, pin, func, instance, args, kwargs):
    integration = litellm._datadog_integration
    model = get_argument_value(args, kwargs, 0, "model", None)
    host = None
    if "host" in kwargs.get("metadata", {}).get("headers", {}):
        host = kwargs["metadata"]["headers"]["host"]
    span = integration.trace(
        pin,
        func.__name__,
        model=model,
        host=host,
        submit_to_llmobs=False,
    )
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

    unwrap(litellm, "completion")
    unwrap(litellm, "acompletion")
    unwrap(litellm, "text_completion")
    unwrap(litellm, "atext_completion")

    delattr(litellm, "_datadog_integration")
