from ddtrace import config
from ddtrace.contrib.trace_utils import set_flattened_tags
from ddtrace.internal.agent import get_stats_url
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.dogstatsd import get_dogstatsd_client

from .. import trace_utils
from ...pin import Pin
from ..trace_utils import wrap
from ._utils import append_tag_prefixes
from ._utils import get_price
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

_statsd = None


def _stats_client():
    global _statsd
    if _statsd is None:
        # FIXME: this currently does not consider if the tracer
        # is configured to use a different hostname.
        # eg. tracer.configure(host="new-hostname")
        _statsd = get_dogstatsd_client(
            get_stats_url(),
            namespace="openai",
            tags=["env:%s" % config.env, "service:%s" % config.service, "version:%s" % config.version],
        )
    return _statsd


def patch():
    # Avoid importing openai at the module level, eventually will be an import hook
    import openai

    if getattr(openai, "__datadog_patch", False):
        return

    wrap(openai, "api_resources.abstract.engine_api_resource.EngineAPIResource.create", patched_create(openai))

    try:
        # Added in v0.27
        import openai.api_resources.chat_completion
    except ImportError:
        pass
    else:
        wrap(openai, "api_resources.chat_completion.ChatCompletion.create", patched_chat_create(openai))

    Pin().onto(openai)
    setattr(openai, "__datadog_patch", True)


def unpatch():
    # FIXME add unpatching. unwrapping the create methods results in a
    # >               return super().create(*args, **kwargs)
    # E               AttributeError: 'method' object has no attribute '__get__'
    pass


@trace_utils.with_traced_module
def patched_chat_create(openai, pin, func, instance, args, kwargs):
    # resource name is set to the model being used -- if that name is not found, use the engine name
    model_name = kwargs.get("model")
    span = pin.tracer.trace(
        "openai.chat.create", resource=model_name, service=trace_utils.ext_service(pin, config.openai)
    )
    try:
        span.set_tag_str("model", model_name)
        resp = func(*args, **kwargs)
        return resp
    finally:
        span.finish()


@trace_utils.with_traced_module
def patched_create(openai, pin, func, instance, args, kwargs):
    model_name = kwargs.get("model")
    metric_tags = ["model:%s" % model_name, "engine:%s" % instance.OBJECT_NAME]
    span = pin.tracer.trace("openai.request", service=trace_utils.ext_service(pin, config.openai))
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
        usage_metrics(resp.get("usage"), model_name, instance.OBJECT_NAME, metric_tags)
        return resp
    except openai.error.OpenAIError as err:
        set_flattened_tags(
            span,
            append_tag_prefixes([RESPONSE_TAG_PREFIX, ERROR_TAG_PREFIX], {"code": err.code, "message": str(err)}),
        )
        _stats_client().increment("error.{}".format(err.__class__.__name__), 1, tags=metric_tags)
        raise err
    finally:
        span.finish()
        _stats_client().distribution("request.duration", span.duration_ns, tags=metric_tags)


def usage_metrics(usage, model, engine, metrics_tags):
    if not usage:
        return
    for token_type in ["prompt", "completion", "total"]:
        num_tokens = usage.get(token_type + "_tokens")
        if not num_tokens:
            continue
        # format metric name into tokens.<token type>
        name = "{}.{}".format("tokens", token_type)
        # want to capture total count for tokens, as well its distribution
        _stats_client().increment(name, num_tokens, tags=metrics_tags)
        _stats_client().distribution(name, num_tokens, tags=metrics_tags)
    # increment price info
    prompt_price = get_price(model, num_tokens, engine, "prompt")
    completion_price = get_price(model, num_tokens, engine, "completion")
    if prompt_price or completion_price:
        _stats_client().increment("cost.prompt", prompt_price, tags=metrics_tags)
        _stats_client().increment("cost.completion", completion_price, tags=metrics_tags)
        _stats_client().increment("cost.total", completion_price + prompt_price, tags=metrics_tags)
