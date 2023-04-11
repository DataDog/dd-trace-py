from ddtrace import config
from ddtrace.contrib.trace_utils import set_flattened_tags
from ddtrace.internal.agent import get_stats_url
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.dogstatsd import get_dogstatsd_client

from .. import trace_utils
from .. import trace_utils_async
from ...pin import Pin
from ..trace_utils import wrap
from ._utils import append_tag_prefixes
from ._utils import engine_resource_name
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
    wrap(openai, "api_resources.abstract.engine_api_resource.EngineAPIResource.acreate", patched_async_create(openai))

    try:
        # Added in v0.27
        import openai.api_resources.chat_completion
    except ImportError:
        pass
    else:
        wrap(openai, "api_resources.chat_completion.ChatCompletion.create", patched_engine_create(openai))
        wrap(openai, "api_resources.chat_completion.ChatCompletion.acreate", patched_async_engine_create(openai))

    try:
        import openai.api_resources.completion
    except ImportError:
        pass
    else:
        wrap(openai, "api_resources.completion.Completion.create", patched_engine_create(openai))
        wrap(openai, "api_resources.completion.Completion.acreate", patched_async_engine_create(openai))

    try:
        import openai.api_resources.embedding
    except ImportError:
        raise
    else:
        wrap(openai, "api_resources.embedding.Embedding.create", patched_engine_create(openai))
        wrap(openai, "api_resources.embedding.Embedding.acreate", patched_async_engine_create(openai))

    Pin().onto(openai)
    setattr(openai, "__datadog_patch", True)


def unpatch():
    # FIXME add unpatching. unwrapping the create methods results in a
    # >               return super().create(*args, **kwargs)
    # E               AttributeError: 'method' object has no attribute '__get__'
    pass


@trace_utils.with_traced_module
def patched_engine_create(openai, pin, func, instance, args, kwargs):
    # resource name is set to the model being used -- if that name is not found, use the engine name
    span = start_engine_span(openai, pin, instance, args, kwargs)
    resp, err = None, None
    try:
        resp = func(*args, **kwargs)
        return resp
    except openai.error.OpenAIError as err:
        err = err
    finally:
        finish_engine_span(span, resp, err, openai, instance, kwargs)



@trace_utils_async.with_traced_module
async def patched_async_engine_create(openai, pin, func, instance, args, kwargs):
    # resource name is set to the model being used -- if that name is not found, use the engine name
    span = start_engine_span(openai, pin, instance, args, kwargs)
    resp, err = None, None
    try:
        resp = await func(*args, **kwargs)
        return resp
    except openai.error.OpenAIError as err:
        err = err
    finally:
        finish_engine_span(span, resp, err, openai, instance, kwargs)



@trace_utils.with_traced_module
def patched_create(openai, pin, func, instance, args, kwargs):
    model_name = kwargs.get("model")
    span = pin.tracer.trace("openai.request", resource=model_name, service=trace_utils.ext_service(pin, config.openai))
    resp, err = None, None
    try:
        span.set_tag_str("model", model_name)
        span.set_tag_str(COMPONENT, config.openai.integration_name)
        span.set_tag_str(ENGINE, instance.OBJECT_NAME)
        resp = func(*args, **kwargs)
        return resp
    except openai.error.OpenAIError as err:
        span.set_tag_str("error", err.__class__.__name__)
        raise err
    finally:
        span.finish()


@trace_utils_async.with_traced_module
async def patched_async_create(openai, pin, func, instance, args, kwargs):
    model_name = kwargs.get("model")
    span = pin.tracer.trace("openai.request", resource=model_name, service=trace_utils.ext_service(pin, config.openai))
    resp, err = None, None
    try:
        span.set_tag_str("model", model_name)
        span.set_tag_str(COMPONENT, config.openai.integration_name)
        span.set_tag_str(ENGINE, instance.OBJECT_NAME)
        resp = await func(*args, **kwargs)
        return resp
    except openai.error.OpenAIError as err:
        span.set_tag_str("error", err.__class__.__name__)
        raise err
    finally:
        span.finish()


def start_engine_span(openai, pin, instance, args, kwargs):
    model_name = kwargs.get("model")
    span = pin.tracer.trace(
        engine_resource_name(openai, instance), resource=model_name, service=trace_utils.ext_service(pin, config.openai)
    )
    span.set_tag_str(COMPONENT, config.openai.integration_name)
    span.set_tag_str(ENGINE, instance.OBJECT_NAME)
    span.set_tag_str("model", model_name)
    set_flattened_tags(
        span,
        append_tag_prefixes([REQUEST_TAG_PREFIX], process_request(openai, instance.OBJECT_NAME, args, kwargs)),
        processor=process_text,
    )
    return span


def finish_engine_span(span, resp, err, openai, instance, kwargs):
    metric_tags = ["model:%s" % kwargs.get("model"), "engine:%s" % instance.OBJECT_NAME]
    if resp:
        set_flattened_tags(
            span,
            append_tag_prefixes([RESPONSE_TAG_PREFIX], process_response(openai, instance.OBJECT_NAME, resp)),
            processor=process_text,
        )
        usage_metrics(resp.get("usage"), metric_tags)
    elif err:
        set_flattened_tags(
            span,
            append_tag_prefixes([RESPONSE_TAG_PREFIX, ERROR_TAG_PREFIX], {"code": err.code, "message": str(err)}),
        )
        _stats_client().increment("error.{}".format(err.__class__.__name__), 1, tags=metric_tags)
        raise err
    span.finish()
    _stats_client().distribution("request.duration", span.duration_ns, tags=metric_tags)


def usage_metrics(usage, metrics_tags):
    if not usage:
        return
    for token_type in ["prompt", "completion", "total"]:
        num_tokens = usage.get(token_type + "_tokens")
        if not num_tokens:
            continue
        # format metric name into tokens.<token type>
        name = "{}.{}".format("tokens", token_type)
        # want to capture total count for token distribution
        _stats_client().distribution(name, num_tokens, tags=metrics_tags)
