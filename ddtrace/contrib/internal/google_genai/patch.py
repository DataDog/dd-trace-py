import sys

from google import genai

from ddtrace import config
from ddtrace.contrib.internal.google_genai._utils import TracedGoogleGenAIStreamResponse
from ddtrace.contrib.internal.google_genai._utils import extract_provider_and_model_name_genai
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.llmobs._integrations import GoogleGenAIIntegration
from ddtrace.trace import Pin


config._add(
    "google_genai",
    {},
)


def _supported_versions():
    return {"google.genai": ">=1.19.0"}


def get_version():
    # type: () -> str
    return getattr(genai, "__version__", "")


@with_traced_module
def traced_generate(genai, pin, func, instance, args, kwargs):
    integration = genai._datadog_integration
    provider_name, model_name = extract_provider_and_model_name_genai(kwargs)
    with integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=False,
    ):
        return func(*args, **kwargs)


@with_traced_module
def traced_generate_stream(genai, pin, func, instance, args, kwargs):
    integration = genai._datadog_integration
    generation_response = None
    provider_name, model_name = extract_provider_and_model_name_genai(kwargs)
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=False,
    )
    try:
        generation_response = func(*args, **kwargs)
        return TracedGoogleGenAIStreamResponse(generation_response, integration, span, args, kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        span.finish()


def patch():
    if getattr(genai, "_datadog_patch", False):
        return

    genai._datadog_patch = True
    Pin().onto(genai)
    integration = GoogleGenAIIntegration(integration_config=config.google_genai)
    genai._datadog_integration = integration

    wrap("google.genai", "models.Models.generate_content", traced_generate(genai))
    wrap("google.genai", "models.Models.generate_content_stream", traced_generate_stream(genai))


def unpatch():
    if not getattr(genai, "_datadog_patch", False):
        return

    genai._datadog_patch = False

    unwrap(genai.models.Models, "generate_content")
    unwrap(genai.models.Models, "generate_content_stream")

    delattr(genai, "_datadog_integration")
