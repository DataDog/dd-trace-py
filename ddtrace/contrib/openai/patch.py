import openai

from ddtrace import config
from ddtrace.internal.constants import COMPONENT
from ddtrace.vendor import wrapt

from ...pin import Pin
from ..trace_utils import unwrap


config._add(
    "openai",
    {
        "distributed_tracing": True,
        "_default_service": "openai",
    },
)


def patch():
    # Do monkey patching here
    if getattr(openai, "__datadog_patch", False):
        return
    setattr(openai, "__datadog_patch", True)
    _w = wrapt.wrap_function_wrapper
    _w("openai", "api_resources.abstract.engine_api_resource.EngineAPIResource.create", patched_create)
    # _w("openai", "Completion.create", traced_create)
    # _w("openai", "OpenAIObject.__init__", traced_init)
    # _w("openai", "OpenAIObject.construct_from", traced_init)


def unpatch():
    # Undo the monkey patching that patch() did here
    if getattr(openai, "__datadog_patch", False):
        setattr(openai, "__datadog_patch", False)
        unwrap(openai.api_resources.abstract.engine_api_resource.EngineAPIResource, "create")
        # _u(openai.OpenAIObject, "__init__")
        # _u(openai.OpenAIObject, "construct_from")


def patched_create(func, instance, args, kwargs):
    pin = Pin.get_from(openai)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    with pin.tracer.trace(instance.OBJECT_NAME) as span:
        span.set_tag_str(COMPONENT, config.openai.integration_name)
        for k, v in args:
            span.set_tag_str(k, v)
        for k, v in kwargs.items():
            span.set_tag_str(k, v)
        try:
            resp = func(*args, **kwargs)
            return resp
        except openai.error.OpenAIError as err:
            span.set_tag("error", str(err))
            span.finish()
            raise err


# OpenAI Object tracing?

# def traced_init(func, instance, args, kwargs):
#     func(*args, **kwargs)

#     tags = {}
#     for arg in ["organization", "api_version", "api_type", "reponse_ms", "api_base", "engine"]:
#         tags[arg] = args.get(arg)
#     # set tracing meta-data for this model
#     pin = Pin(service=args.get("organization"), tags=tags)
#     pin.onto(instance)

# def traced_construct(func, instance, args, kwargs):
#     tags = {}
#     for arg in ["organization", "api_version", "api_type", "reponse_ms", "api_base", "engine"]:
#         tags[arg] = args.get(arg)
#     obj = func(*args, **kwargs)
#     # set tracing meta-data for this model
#     pin = Pin(service=args.get("organization"))
#     pin.onto(obj)
#     return obj

# def traced_refresh(func, instance, args, kwargs):
#     # get the pin from the object first
#     pass
