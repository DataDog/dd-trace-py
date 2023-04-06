import openai

from ddtrace import config
from ddtrace.contrib.trace_utils import set_flattened_tags
from ddtrace.internal.constants import COMPONENT
from ddtrace.vendor import wrapt

from ...pin import Pin
from ..trace_utils import unwrap
from .utils import append_tag_prefixes
from .utils import process_request
from .utils import process_response


config._add(
    "openai",
    {
        "distributed_tracing": True,
        "_default_service": "openai",
    },
)

REQUEST_TAG_PREFIX = "request"
RESPONSE_TAG_PREFIX = "response"
ERROR_TAG_PREFIX = "error"
ENGINE = "engine"
RESOURCE_NAME = "model"


def patch():
    # Do monkey patching here
    if getattr(openai, "__datadog_patch", False):
        return
    setattr(openai, "__datadog_patch", True)
    _w = wrapt.wrap_function_wrapper
    _w("openai", "api_resources.abstract.engine_api_resource.EngineAPIResource.create", patched_create)
    Pin().onto(openai)


def unpatch():
    # Undo the monkey patching that patch() did here
    if getattr(openai, "__datadog_patch", False):
        setattr(openai, "__datadog_patch", False)
        unwrap(openai.api_resources.abstract.engine_api_resource.EngineAPIResource, "create")


def patched_create(func, instance, args, kwargs):
    pin = Pin.get_from(openai)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)
    # resource name is set to the model being used -- if that name is not found, use the engine name
    sname = kwargs.get(RESOURCE_NAME) if kwargs.get(RESOURCE_NAME) is not None else instance.OBJECT_NAME
    with pin.tracer.trace(sname) as span:
        span.set_tag_str(COMPONENT, config.openai.integration_name)
        span.set_tag_str(ENGINE, instance.OBJECT_NAME)
        set_flattened_tags(
            span, append_tag_prefixes([REQUEST_TAG_PREFIX], process_request(instance.OBJECT_NAME, args, kwargs))
        )
        resp = {}
        try:
            resp = func(*args, **kwargs)
            set_flattened_tags(
                span, append_tag_prefixes([RESPONSE_TAG_PREFIX], process_response(instance.OBJECT_NAME, resp))
            )
            return resp
        except openai.error.OpenAIError as err:
            set_flattened_tags(
                span,
                append_tag_prefixes([RESPONSE_TAG_PREFIX, ERROR_TAG_PREFIX], {"code": err.code, "message": str(err)}),
            )
            span.finish()
            raise err
