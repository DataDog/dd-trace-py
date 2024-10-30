import sys

import vertexai

from ddtrace import config
from ddtrace.contrib.internal.vertexai._utils import _extract_model_name
from ddtrace.contrib.internal.vertexai._utils import tag_request
from ddtrace.contrib.internal.vertexai._utils import tag_response
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.llmobs._integrations import VertexAIIntegration
from ddtrace.pin import Pin

config._add(
    "vertexai",
    {},
)

@with_traced_module
def traced_generate(vertexai, pin, func, instance, args, kwargs):
    integration = vertexai._datadog_integration
    generations = None
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider="google",
        model=_extract_model_name(instance),
        submit_to_llmobs=True,
    )
    try:
        tag_request(span, integration, instance, args, kwargs)
        generations = func(*args, **kwargs)

        tag_response(span, generations, integration, instance)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    span.finish()
    return generations


def patch():
    if getattr(vertexai, "_datadog_patch", False):
        return

    vertexai._datadog_patch = True

    Pin().onto(vertexai)
    integration = VertexAIIntegration(integration_config=config)
    vertexai._datadog_integration = integration

    wrap("vertexai", "generative_models.GenerativeModel.generate_content", traced_generate(vertexai))


def unpatch():
    if not getattr(vertexai, "_datadog_patch", False):
        return

    vertexai._datadog_patch = False

    unwrap(vertexai.generative_models.GenerativeModel, "generate_content")
    unwrap(vertexai.generative_models.GenerativeModel, "generate_content_async")

    delattr(vertexai, "_datadog_integration")
