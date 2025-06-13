import os
import sys

from google import genai

from ddtrace import config

# from ddtrace.contrib.internal.google_genai._utils import tag_request
# from ddtrace.contrib.internal.google_genai._utils import tag_response
from ddtrace.contrib.internal.google_genai._utils import extract_provider_and_model_name_genai
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.llmobs._integrations import GoogleGenAIIntegration
from ddtrace.trace import Pin


config._add(
    "google_genai",
    {
        "span_prompt_completion_sample_rate": float(
            os.getenv("DD_GOOGLE_GENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE", 1.0)
        ),
        "span_char_limit": int(os.getenv("DD_GOOGLE_GENAI_SPAN_CHAR_LIMIT", 128)),
    },
)


def get_version():
    # type: () -> str
    return getattr(genai, "__version__", "")


@with_traced_module
def traced_generate(genai, pin, func, instance, args, kwargs):
    integration = genai._datadog_integration
    generation_response = None
    provider_name, model_name = extract_provider_and_model_name_genai(kwargs)
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=False,  # TODO: change to True when we eventually submit to llmobs
    )
    try:
        generation_response = func(*args, **kwargs)
    except:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        span.finish()
    return generation_response


@with_traced_module
def traced_generate_stream(genai, pin, func, instance, args, kwargs):
    pass


def patch():
    if getattr(genai, "_datadog_patch", False):
        return

    genai._datadog_patch = True
    Pin().onto(genai)
    integration = GoogleGenAIIntegration(integration_config=config.google_genai)
    genai._datadog_integration = integration

    wrap("google.genai", "models.Models.generate_content", traced_generate(genai))


def unpatch():
    if not getattr(genai, "_datadog_patch", False):
        return

    genai._datadog_patch = False

    unwrap(genai.models.Models, "generate_content")

    delattr(genai, "_datadog_integration")
