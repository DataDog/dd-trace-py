import os
import random
import sys

from ddtrace import config
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool

from . import _log as ddlogs
from .. import trace_utils
from .. import trace_utils_async
from ...pin import Pin
from ..trace_utils import wrap
from ._metrics import stats_client


config._add(
    "openai",
    {
        "logs_enabled": asbool(os.getenv("DD_OPENAI_LOGS_ENABLED", False)),
        "metrics_enabled": asbool(os.getenv("DD_OPENAI_METRICS_ENABLED", True)),
        "prompt_completion_sample_rate": float(os.getenv("DD_OPENAI_PROMPT_COMPLETION_SAMPLE_RATE", 1.0)),
        # TODO: truncate threshold on prompts/completions
        "_default_service": "openai",
    },
)


log = get_logger(__file__)


def _openai_log(*args, **kwargs):
    if not config.openai.logs_enabled:
        return
    ddlogs.log(*args, **kwargs)


def patch():
    # Avoid importing openai at the module level, eventually will be an import hook
    import openai

    if getattr(openai, "__datadog_patch", False):
        return

    # The requests integration sets a default service name of `requests` which hides
    # the real response time of the request to OpenAI.
    # FIXME: try to set a pin on the requests instance that the openAI library uses
    #        so that we only override it for that instance.
    #  Pin.clone(service="openai").onto(openai.web_....requests.ClientSession)
    config.requests._default_service = None

    if hasattr(openai.api_resources, "completion"):
        wrap(openai, "api_resources.completion.Completion.create", patched_completion_create(openai))
        wrap(openai, "api_resources.completion.Completion.acreate", patched_completion_acreate(openai))

    if hasattr(openai.api_resources, "chat_completion"):
        wrap(openai, "api_resources.chat_completion.ChatCompletion.create", patched_chat_completion_create(openai))
        wrap(openai, "api_resources.chat_completion.ChatCompletion.acreate", patched_chat_completion_acreate(openai))

    if hasattr(openai.api_resources, "embedding"):
        wrap(openai, "api_resources.embedding.Embedding.create", patched_embedding_create(openai))
        wrap(openai, "api_resources.embedding.Embedding.acreate", patched_embedding_acreate(openai))

    Pin().onto(openai)
    setattr(openai, "__datadog_patch", True)

    if config.openai.logs_enabled:
        ddsite = os.getenv("DD_SITE", "datadoghq.com")
        ddapikey = os.getenv("DD_API_KEY")
        if not ddapikey:
            raise ValueError("DD_API_KEY is required for sending logs from the OpenAI integration")

        ddlogs.start(site=ddsite, api_key=ddapikey)
        # FIXME: these logs don't show up when DD_TRACE_DEBUG=1 set... same thing for all contribs?
        log.debug("started log writer")


def unpatch():
    # FIXME add unpatching. unwrapping the create methods results in a
    # >               return super().create(*args, **kwargs)
    # E               AttributeError: 'method' object has no attribute '__get__'
    pass


@trace_utils.with_traced_module
def patched_completion_create(openai, pin, func, instance, args, kwargs):
    g = _completion_create(openai, pin, instance, args, kwargs)
    g.send(None)
    resp, resp_err = None, None
    try:
        resp = func(*args, **kwargs)
        return resp
    except Exception as err:
        resp_err = err
        raise
    finally:
        try:
            g.send((resp, resp_err))
        except StopIteration:
            # expected
            pass


@trace_utils_async.with_traced_module
async def patched_completion_acreate(openai, pin, func, instance, args, kwargs):
    g = _completion_create(openai, pin, instance, args, kwargs)
    g.send(None)
    resp, resp_err = None, None
    try:
        resp = await func(*args, **kwargs)
        return resp
    except Exception as err:
        resp_err = err
        raise
    finally:
        try:
            g.send((resp, resp_err))
        except StopIteration:
            # expected
            pass


@trace_utils.with_traced_module
def patched_chat_completion_create(openai, pin, func, instance, args, kwargs):
    g = _chat_completion_create(openai, pin, instance, args, kwargs)
    g.send(None)
    resp, resp_err = None, None
    try:
        resp = func(*args, **kwargs)
        return resp
    except Exception as err:
        resp_err = err
        raise
    finally:
        try:
            g.send((resp, resp_err))
        except StopIteration:
            # expected
            pass


@trace_utils_async.with_traced_module
async def patched_chat_completion_acreate(openai, pin, func, instance, args, kwargs):
    g = _chat_completion_create(openai, pin, instance, args, kwargs)
    g.send(None)
    resp, resp_err = None, None
    try:
        resp = await func(*args, **kwargs)
        return resp
    except Exception as err:
        resp_err = err
        raise
    finally:
        try:
            g.send((resp, resp_err))
        except StopIteration:
            # expected
            pass


@trace_utils.with_traced_module
def patched_embedding_create(openai, pin, func, instance, args, kwargs):
    g = _embedding_create(openai, pin, instance, args, kwargs)
    g.send(None)
    resp, resp_err = None, None
    try:
        resp = func(*args, **kwargs)
        return resp
    except Exception as err:
        resp_err = err
        raise
    finally:
        try:
            g.send((resp, resp_err))
        except StopIteration:
            # expected
            pass


@trace_utils_async.with_traced_module
async def patched_embedding_acreate(openai, pin, func, instance, args, kwargs):
    g = _embedding_create(openai, pin, instance, args, kwargs)
    g.send(None)
    resp, resp_err = None, None
    try:
        resp = await func(*args, **kwargs)
        return resp
    except Exception as err:
        resp_err = err
        raise
    finally:
        try:
            g.send((resp, resp_err))
        except StopIteration:
            # expected
            pass


# set basic openai data for all openai spans
def init_openai_span(span, openai):
    span.set_tag_str(COMPONENT, config.openai.integration_name)
    if hasattr(openai, "api_base") and openai.api_base:
        span.set_tag_str("api_base", openai.api_base)
    if hasattr(openai, "api_version") and openai.api_version:
        span.set_tag_str("api_version", openai.api_version)
    if hasattr(openai, "organization") and openai.organization:
        span.set_tag_str("organization", openai.organization)


_completion_request_attrs = [
    "model",
    "suffix",
    "max_tokens",
    "temperature",
    "top_p",
    "n",
    "stream",
    "logprobs",
    "echo",
    "stop",
    "presence_penalty",
    "frequency_penalty",
    "best_of",
    "logit_bias",
    "user",
    "prompt",
]


def _completion_create(openai, pin, instance, args, kwargs):
    model = kwargs.get("model")
    span = pin.tracer.trace(
        "openai.request", resource="completions/%s" % model, service=trace_utils.ext_service(pin, config.openai)
    )
    init_openai_span(span, openai)
    if model:
        span.set_tag_str("model", model)

    sample_prompt_completion = random.randrange(100) < (config.openai.prompt_completion_sample_rate * 100)

    prompt = kwargs.get("prompt")
    if sample_prompt_completion:
        if isinstance(prompt, list):
            for idx, p in enumerate(prompt):
                span.set_tag_str("request.prompt.%d" % idx, _process_text(p))
        else:
            span.set_tag_str("request.prompt", _process_text(prompt))

    for kw_attr in _completion_request_attrs:
        if kw_attr in kwargs:
            if kw_attr != "prompt":
                span.set_tag("request.%s" % kw_attr, kwargs[kw_attr])

    resp, error = yield span

    metric_tags = [
        "model:%s" % kwargs.get("model"),
        "endpoint:%s" % instance.OBJECT_NAME,
        "error:%d" % (1 if error else 0),
    ]
    if openai.organization:
        metric_tags.append("organization:%s" % openai.organization)

    if error is not None:
        span.set_exc_info(*sys.exc_info())
        stats_client().increment("error", 1, tags=metric_tags + ["error_type:%s" % error.__class__.__name__])
    if resp:
        if "choices" in resp:
            choices = resp["choices"]
            span.set_tag("response.choices.num", len(choices))
            for choice in choices:
                idx = choice["index"]
                if choice.get("finish_reason"):
                    span.set_tag_str("response.choices.%d.finish_reason" % idx, choice.get("finish_reason"))
                if choice.get("logprobs"):
                    span.set_tag("response.choices.%d.logprobs" % idx, choice.get("logprobs"))
                if sample_prompt_completion:
                    span.set_tag("response.choices.%d.text" % idx, _process_text(choice.get("text")))
        span.set_tag("response.id", resp["id"])
        span.set_tag("response.object", resp["object"])
        for token_type in ["completion_tokens", "prompt_tokens", "total_tokens"]:
            if token_type in resp["usage"]:
                span.set_tag("response.usage.%s" % token_type, resp["usage"][token_type])
        _usage_metrics(resp.get("usage"), metric_tags)

    if sample_prompt_completion:
        _openai_log(
            "info" if error is None else "error",
            "sampled completion",
            tags=metric_tags,
            attrs={
                "prompt": prompt,
                "choices": resp["choices"] if resp and "choices" in resp else [],
            },
        )
    span.finish()
    stats_client().distribution("request.duration", span.duration_ns, tags=metric_tags)


_chat_completion_request_attrs = [
    "model",
    "messages",
    "temperature",
    "top_p",
    "n",
    "stream",
    "stop",
    "max_tokens",
    "presence_penalty",
    "frequency_penalty",
    "logit_bias",
    "user",
]


def _chat_completion_create(openai, pin, instance, args, kwargs):
    model = kwargs.get("model")
    span = pin.tracer.trace(
        "openai.request", resource="chat.completions/%s" % model, service=trace_utils.ext_service(pin, config.openai)
    )
    init_openai_span(span, openai)
    if model:
        span.set_tag_str("model", model)

    sample_prompt_completion = random.randrange(100) < (config.openai.prompt_completion_sample_rate * 100)

    messages = kwargs.get("messages")
    if sample_prompt_completion:

        def set_message_tag(message):
            content = _process_text(message.get("content"))
            role = _process_text(message.get("role"))
            span.set_tag_str("request.messages.%d.content" % idx, content)
            span.set_tag_str("request.messages.%d.role" % idx, role)

        if isinstance(messages, list):
            for idx, message in enumerate(messages):
                set_message_tag(message)
        else:
            set_message_tag(messages)

    for kw_attr in _chat_completion_request_attrs:
        if kw_attr in kwargs:
            if kw_attr != "messages":
                span.set_tag("request.%s" % kw_attr, kwargs[kw_attr])

    resp, error = yield span

    metric_tags = [
        "model:%s" % kwargs.get("model"),
        "endpoint:%s" % instance.OBJECT_NAME,
        "error:%d" % (1 if error else 0),
    ]
    if openai.organization:
        metric_tags.append("organization:%s" % openai.organization)

    completions = ""

    if error is not None:
        span.set_exc_info(*sys.exc_info())
        stats_client().increment("error", 1, tags=metric_tags + ["error_type:%s" % error.__class__.__name__])
    if resp:
        if "choices" in resp:
            choices = resp["choices"]
            completions = choices
            span.set_tag("response.choices.num", len(choices))
            for choice in choices:
                idx = choice["index"]
                span.set_tag_str("response.choices.%d.finish_reason" % idx, choice.get("finish_reason"))
                span.set_tag("response.choices.%d.logprobs" % idx, choice.get("logprobs"))
                if sample_prompt_completion and choice.get("message"):
                    span.set_tag(
                        "response.choices.%d.message.content" % idx, _process_text(choice.get("message").get("content"))
                    )
                    span.set_tag(
                        "response.choices.%d.message.role" % idx, _process_text(choice.get("message").get("role"))
                    )
        span.set_tag("response.id", resp["id"])
        span.set_tag("response.object", resp["object"])
        for token_type in ["completion_tokens", "prompt_tokens", "total_tokens"]:
            if token_type in resp["usage"]:
                span.set_tag("response.usage.%s" % token_type, resp["usage"][token_type])
        _usage_metrics(resp.get("usage"), metric_tags)

    if sample_prompt_completion:
        _openai_log(
            "info" if error is None else "error",
            "sampled chat completion",
            tags=metric_tags,
            attrs={
                "messages": messages,
                "completion": completions,
            },
        )
    span.finish()
    stats_client().distribution("request.duration", span.duration_ns, tags=metric_tags)


def _embedding_create(openai, pin, instance, args, kwargs):
    model = kwargs.get("model")
    span = pin.tracer.trace(
        "openai.request", resource="embedding/%s" % model, service=trace_utils.ext_service(pin, config.openai)
    )
    init_openai_span(span, openai)
    if model:
        span.set_tag_str("model", model)

    for kw_attr in ["model", "input", "user"]:
        if kw_attr in kwargs:
            if kw_attr == "input" and isinstance(kwargs["input"], list):
                for idx, inp in enumerate(kwargs["input"]):
                    span.set_tag_str("request.input.%d" % idx, _process_text(inp))
            span.set_tag("request.%s" % kw_attr, kwargs[kw_attr])

    resp, error = yield span

    metric_tags = [
        "model:%s" % kwargs.get("model"),
        "endpoint:%s" % instance.OBJECT_NAME,
        "error:%d" % (1 if error else 0),
    ]
    if openai.organization:
        metric_tags.append("organization:%s" % openai.organization)
    if error is not None:
        span.set_exc_info(*sys.exc_info())
        stats_client().increment("error", 1, tags=metric_tags + ["error_type:%s" % error.__class__.__name__])
    if resp:
        if "data" in resp:
            span.set_tag("response.data.num-embeddings", len(resp["data"]))
            span.set_tag("response.data.embedding-length", len(resp["data"][0]["embedding"]))
        for kw_attr in ["model", "object", "usage"]:
            if kw_attr in kwargs:
                span.set_tag("response.%s" % kw_attr, kwargs[kw_attr])

        _usage_metrics(resp.get("usage"), metric_tags)

    span.finish()
    stats_client().distribution("request.duration", span.duration_ns, tags=metric_tags)


def _usage_metrics(usage, metrics_tags):
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


def _process_text(text, truncate=512):
    text = " ".join(text.split())
    if len(text) > truncate:
        text = text[: truncate - 14] + "[TRUNCATED...]"
    return text
