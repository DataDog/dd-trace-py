import os
import typing

from ddtrace import config
from ddtrace.contrib.trace_utils import set_flattened_tags
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool

from .. import trace_utils
from .. import trace_utils_async
from ...pin import Pin
from ..trace_utils import wrap
from ._logging import V2LogWriter
from ._metrics import stats_client
from ._openai import CHAT_COMPLETIONS
from ._openai import COMPLETIONS
from ._openai import EMBEDDINGS
from ._openai import process_request
from ._openai import process_response
from ._openai import supported
from ._utils import append_tag_prefixes
from ._utils import process_text


if typing.TYPE_CHECKING:
    from typing import List


config._add(
    "openai",
    {
        "logs_enabled": asbool(os.getenv("DD_OPENAI_LOGS_ENABLED", False)),
        "metrics_enabled": asbool(os.getenv("DD_OPENAI_METRICS_ENABLED", True)),
        "prompt_completion_sample_rate": float(os.getenv("DD_OPENAI_PROMPT_COMPLETION_SAMPLE_RATE", 1.0)),
        "_default_service": "openai",
    },
)


log = get_logger(__file__)


REQUEST_TAG_PREFIX = "request"
RESPONSE_TAG_PREFIX = "response"
ERROR_TAG_PREFIX = "error"


_logs_writer = None


def _log(level, msg, tags):
    # type: (str, str, List[str]) -> None
    global _logs_writer

    if _logs_writer is None or config.openai.logs_enabled is False:
        return

    import datetime

    timestamp = datetime.datetime.now().isoformat()
    from ddtrace import tracer

    curspan = tracer.current_span()

    log = {
        "message": "%s %s %s" % (timestamp, level, msg),
        "hostname": os.getenv("DD_HOSTNAME", get_hostname()),
        "ddsource": "python",
        "service": "openai",
    }
    if config.env:
        tags.append("env:%s" % config.env)
    if config.version:
        tags.append("version:%s" % config.version)
    log["ddtags"] = ",".join(t for t in tags)
    log["prompt"] = "hello world"
    log[
        "completion"
    ] = "A Hello, World! program is generally a computer program that ignores any input and outputs or displays a message similar to Hello, World!."
    log["dd.trace_id"] = str(curspan.trace_id)
    log["dd.span_id"] = str(curspan.span_id)
    _logs_writer.enqueue(log)


def patch():
    # Avoid importing openai at the module level, eventually will be an import hook
    import openai

    if getattr(openai, "__datadog_patch", False):
        return

    wrap(openai, "api_resources.abstract.engine_api_resource.EngineAPIResource.create", patched_create(openai))
    wrap(openai, "api_resources.abstract.engine_api_resource.EngineAPIResource.acreate", patched_async_create(openai))

    if supported(CHAT_COMPLETIONS):
        wrap(openai, "api_resources.chat_completion.ChatCompletion.create", patched_endpoint(openai))
        wrap(openai, "api_resources.chat_completion.ChatCompletion.acreate", patched_async_endpoint(openai))

    if supported(COMPLETIONS):
        wrap(openai, "api_resources.completion.Completion.create", patched_endpoint(openai))
        wrap(openai, "api_resources.completion.Completion.acreate", patched_async_endpoint(openai))

    if supported(EMBEDDINGS):
        wrap(openai, "api_resources.embedding.Embedding.create", patched_endpoint(openai))
        wrap(openai, "api_resources.embedding.Embedding.acreate", patched_async_endpoint(openai))

    Pin().onto(openai)
    setattr(openai, "__datadog_patch", True)

    if config.openai.logs_enabled:
        ddsite = (os.getenv("DD_SITE", "datadoghq.com"),)
        ddapikey = os.getenv("DD_API_KEY")
        # TODO: replace with proper check
        assert ddapikey

        writer = V2LogWriter(
            site=ddsite,
            api_key=ddapikey,
            interval=1.0,
            timeout=2.0,
        )
        global _logs_writer
        _logs_writer = writer
        _logs_writer.start()
        # TODO: these logs don't show up when DD_TRACE_DEBUG=1 set... same thing for all contribs?
        log.debug("started log writer")


def unpatch():
    # FIXME add unpatching. unwrapping the create methods results in a
    # >               return super().create(*args, **kwargs)
    # E               AttributeError: 'method' object has no attribute '__get__'
    pass


@trace_utils.with_traced_module
def patched_endpoint(openai, pin, func, instance, args, kwargs):
    # resource name is set to the model being used -- if that name is not found, use the engine name
    span = start_endpoint_span(openai, pin, instance, args, kwargs)
    resp, resp_err = None, None
    _log("INFO", "test prompt", tags=["model:%s" % kwargs.get("model")])
    try:
        resp = func(*args, **kwargs)
        return resp
    except openai.error.OpenAIError as err:
        resp_err = err
    finally:
        finish_endpoint_span(span, resp, resp_err, openai, instance, kwargs)


@trace_utils_async.with_traced_module
async def patched_async_endpoint(openai, pin, func, instance, args, kwargs):
    # resource name is set to the model being used -- if that name is not found, use the engine name
    span = start_endpoint_span(openai, pin, instance, args, kwargs)
    resp, resp_err = None, None
    try:
        resp = await func(*args, **kwargs)
        return resp
    except openai.error.OpenAIError as err:
        resp_err = err
    finally:
        finish_endpoint_span(span, resp, resp_err, openai, instance, kwargs)


@trace_utils.with_traced_module
def patched_create(openai, pin, func, instance, args, kwargs):
    span = pin.tracer.trace(
        "openai.request", resource=instance.OBJECT_NAME, service=trace_utils.ext_service(pin, config.openai)
    )
    try:
        init_openai_span(span, openai, kwargs.get("model"))
        resp = func(*args, **kwargs)
        return resp
    except openai.error.OpenAIError as err:
        span.set_tag_str("error", err.__class__.__name__)
        raise err
    finally:
        span.finish()


@trace_utils_async.with_traced_module
async def patched_async_create(openai, pin, func, instance, args, kwargs):
    span = pin.tracer.trace(
        "openai.request", resource=instance.OBJECT_NAME, service=trace_utils.ext_service(pin, config.openai)
    )
    try:
        init_openai_span(span, openai, kwargs.get("model"))
        resp = await func(*args, **kwargs)
        return resp
    except openai.error.OpenAIError as err:
        span.set_tag_str("error", err.__class__.__name__)
        raise err
    finally:
        span.finish()


# set basic openai data for all openai spans
def init_openai_span(span, openai, model):
    if model:
        span.set_tag_str("model", model)
    span.set_tag_str(COMPONENT, config.openai.integration_name)
    if hasattr(openai, "api_base") and openai.api_base:
        span.set_tag_str("api_base", openai.api_base)
    if hasattr(openai, "api_version") and openai.api_version:
        span.set_tag_str("api_version", openai.api_version)


def start_endpoint_span(openai, pin, instance, args, kwargs):
    span = pin.tracer.trace(
        "openai.create", resource=instance.OBJECT_NAME, service=trace_utils.ext_service(pin, config.openai)
    )
    init_openai_span(span, openai, kwargs.get("model"))
    set_flattened_tags(
        span,
        append_tag_prefixes([REQUEST_TAG_PREFIX], process_request(openai, instance.OBJECT_NAME, args, kwargs)),
        processor=process_text,
    )
    return span


def finish_endpoint_span(span, resp, err, openai, instance, kwargs):
    metric_tags = ["model:%s" % kwargs.get("model"), "endpoint:%s" % instance.OBJECT_NAME]
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
        stats_client().increment("error.{}".format(err.__class__.__name__), 1, tags=metric_tags)
        span.finish()
        raise err
    span.finish()
    stats_client().distribution("request.duration", span.duration_ns, tags=metric_tags)


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
        stats_client().distribution(name, num_tokens, tags=metrics_tags)
