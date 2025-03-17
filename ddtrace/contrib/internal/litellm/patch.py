import os
import sys

import litellm

from ddtrace import config
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.llmobs._integrations import LiteLLMIntegration
from ddtrace.trace import Pin


config._add(
    "litellm",
    {},
)


def get_version():
    # type: () -> str
    return getattr(litellm, "__version__", "")


@with_traced_module
def traced_completion(litellm, pin, func, instance, args, kwargs):
    integration = litellm._datadog_integration
    stream = kwargs.get("stream", False)
    generations = None
    span = integration.trace(
        pin,
        "litellm.%s" % func.__name__,
        submit_to_llmobs=False,
    )
    try:
        generations = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        # streamed spans will be finished separately once the stream generator is exhausted
        if span.error or not stream:
            span.finish()
    return generations



def patch():
    if getattr(litellm, "_datadog_patch", False):
        return

    litellm._datadog_patch = True

    Pin().onto(litellm)
    integration = LiteLLMIntegration(integration_config=config.litellm)
    litellm._datadog_integration = integration

    wrap("litellm", "litellm.completion", traced_completion(litellm))


def unpatch():
    if not getattr(litellm, "_datadog_patch", False):
        return

    litellm._datadog_patch = False

    unwrap("litellm", "litellm.litellm.completion", traced_completion(litellm))

    delattr(litellm, "_datadog_integration")