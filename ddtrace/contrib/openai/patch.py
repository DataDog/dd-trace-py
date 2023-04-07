from ddtrace import config
from ddtrace.contrib.trace_utils import set_flattened_tags
from ddtrace.internal.constants import COMPONENT
from ddtrace.vendor import wrapt

from .. import trace_utils
from ...pin import Pin
from ..trace_utils import unwrap
from ._utils import append_tag_prefixes
from ._utils import infer_object_name
from ._utils import process_request
from ._utils import process_response
from ._utils import process_text


config._add(
    "openai",
    {
        "_default_service": "openai",
    },
)

REQUEST_TAG_PREFIX = "request"
RESPONSE_TAG_PREFIX = "response"
ERROR_TAG_PREFIX = "error"
ENGINE = "engine"


def patch():
    # Avoid importing openai at the module level, eventually will be an import hook
    import openai

    if getattr(openai, "__datadog_patch", False):
        return

    _w = wrapt.wrap_function_wrapper
    _w("openai", "api_resources.abstract.engine_api_resource.EngineAPIResource.create", patched_create(openai))
    Pin().onto(openai)

    setattr(openai, "__datadog_patch", True)


def unpatch():
    import openai

    if getattr(openai, "__datadog_patch", False):
        setattr(openai, "__datadog_patch", False)
        unwrap(openai.api_resources.abstract.engine_api_resource.EngineAPIResource, "create")


@trace_utils.with_traced_module
def patched_create(openai, pin, func, instance, args, kwargs):
    # resource name is set to the model being used -- if that name is not found, use the engine name
    if not hasattr(instance, "OBJECT_NAME"):
        setattr(instance, "OBJECT_NAME", infer_object_name(kwargs))
    resource = kwargs.get("model") if kwargs.get("model") is not None else instance.OBJECT_NAME
    with pin.tracer.trace(
        "openai.request", resource=resource, service=trace_utils.ext_service(pin, config.openai)
    ) as span:
        span.set_tag_str(COMPONENT, config.openai.integration_name)
        span.set_tag_str(ENGINE, instance.OBJECT_NAME)
        set_flattened_tags(
            span,
            append_tag_prefixes([REQUEST_TAG_PREFIX], process_request(openai, instance.OBJECT_NAME, args, kwargs)),
            processor=process_text,
        )
        try:
            resp = func(*args, **kwargs)
            set_flattened_tags(
                span,
                append_tag_prefixes([RESPONSE_TAG_PREFIX], process_response(openai, instance.OBJECT_NAME, resp)),
                processor=process_text,
            )
            return resp
        except openai.error.OpenAIError as err:
            set_flattened_tags(
                span,
                append_tag_prefixes([RESPONSE_TAG_PREFIX, ERROR_TAG_PREFIX], {"code": err.code, "message": str(err)}),
            )
            raise err
