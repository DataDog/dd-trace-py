import os
import sys

from google import genai

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.llmobs._integrations import GoogleGenAIIntegration
from ddtrace.contrib.internal.google_genai._utils import tag_request
from ddtrace.contrib.internal.google_genai._utils import tag_response
from ddtrace.contrib.internal.google_genai._utils import extract_model_name_google_genai
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
    stream = kwargs.get("stream", False)
    generations = None
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider="google",
        # model = extract_model_name_google_genai(kwargs.get("model", "")),
        submit_to_llmobs=True,
    )
    tag_request(span, integration, instance, args, kwargs)
    # try:
    #     tag_request(span, integration, instance, args, kwargs)
    #     generations = func(*args, **kwargs)
    #     if stream:
    #         pass # TODO: handle streamed responses
    #     tag_response(span, generations, integration, instance)
    # except:
    #     span.set_exc_info(*sys.exc_info())
    #     raise
    # finally:
    #     # streamed spans finished separately when stream generator is exhausted
    #     if span.error or not stream:
    #         span.finish()
    # return generations




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