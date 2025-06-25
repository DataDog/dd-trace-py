import sys

from google import genai

from ddtrace import config
from ddtrace.contrib.internal.google_genai._utils import TracedAsyncGoogleGenAIStreamResponse
from ddtrace.contrib.internal.google_genai._utils import TracedGoogleGenAIStreamResponse
from ddtrace.contrib.internal.google_genai._utils import extract_provider_and_model_name
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.llmobs._integrations import GoogleGenAIIntegration
from ddtrace.trace import Pin


config._add("google_genai", {})


def _supported_versions():
    return {"google.genai": ">=1.21.1"}


def get_version() -> str:
    return getattr(genai, "__version__", "")


@with_traced_module
def traced_generate(genai, pin, func, instance, args, kwargs):
    integration = genai._datadog_integration
    provider_name, model_name = extract_provider_and_model_name(kwargs)
    with integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=False,
    ):
        return func(*args, **kwargs)


@with_traced_module
async def traced_async_generate(genai, pin, func, instance, args, kwargs):
    integration = genai._datadog_integration
    provider_name, model_name = extract_provider_and_model_name(kwargs)
    with integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=False,
    ):
        return await func(*args, **kwargs)


@with_traced_module
def traced_generate_stream(genai, pin, func, instance, args, kwargs):
    integration = genai._datadog_integration
    resp = None
    provider_name, model_name = extract_provider_and_model_name(kwargs)
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=False,
    )
    try:
        resp = func(*args, **kwargs)
        return TracedGoogleGenAIStreamResponse(resp, span)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        if span.error:
            span.finish()


@with_traced_module
async def traced_async_generate_stream(genai, pin, func, instance, args, kwargs):
    integration = genai._datadog_integration
    resp = None
    provider_name, model_name = extract_provider_and_model_name(kwargs)
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=False,
    )
    try:
        resp = await func(*args, **kwargs)
        return TracedAsyncGoogleGenAIStreamResponse(resp, span)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        if span.error:
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
    wrap("google.genai", "models.AsyncModels.generate_content", traced_async_generate(genai))
    wrap("google.genai", "models.AsyncModels.generate_content_stream", traced_async_generate_stream(genai))


def unpatch():
    if not getattr(genai, "_datadog_patch", False):
        return

    genai._datadog_patch = False

    unwrap(genai.models.Models, "generate_content")
    unwrap(genai.models.Models, "generate_content_stream")
    unwrap(genai.models.AsyncModels, "generate_content")
    unwrap(genai.models.AsyncModels, "generate_content_stream")

    delattr(genai, "_datadog_integration")
