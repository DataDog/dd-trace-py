import sys

import litellm

from ddtrace import config
from ddtrace.contrib.internal.litellm import _usage_metrics
from ddtrace.contrib.internal.litellm import _usage_metrics_writer
from ddtrace.contrib.internal.litellm.utils import LiteLLMAsyncStreamHandler
from ddtrace.contrib.internal.litellm.utils import LiteLLMStreamHandler
from ddtrace.contrib.internal.litellm.utils import extract_host_tag
from ddtrace.contrib.internal.stream_handler import make_traced_stream
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal import atexit
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.settings import env
from ddtrace.internal.settings._opentelemetry import otel_config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.llmobs._constants import LITELLM_ROUTER_INSTANCE_KEY
from ddtrace.llmobs._integrations import LiteLLMIntegration


log = get_logger(__name__)

config._add(
    "litellm",
    {
        "usage_metrics_enabled": asbool(env.get("DD_LITELLM_USAGE_METRICS_ENABLED", default=False)),
        "usage_metrics_tags": env.get("DD_LITELLM_USAGE_METRICS_TAGS"),
        "usage_metrics_exporter": env.get("DD_LITELLM_USAGE_METRICS_EXPORTER", default="otlp"),
        "usage_metrics_client_source": env.get("DD_LITELLM_USAGE_METRICS_CLIENT_SOURCE"),
    },
)


def get_version() -> str:
    version_module = getattr(litellm, "_version", None)
    return getattr(version_module, "version", "")


def _supported_versions() -> dict[str, str]:
    return {"litellm": "*"}


def _handle_router_stream_response(resp, span, kwargs, instance, integration, args, is_async=False):
    """
    Handle router streaming responses with fallback for different wrapper types.

    In litellm>=1.74.15, router streaming responses may be wrapped in FallbackStreamWrapper
    (for mid-stream fallback support) or other types that don't expose the .handler attribute.
    """
    if hasattr(resp, "handler") and hasattr(resp.handler, "add_span"):
        resp.handler.add_span(span, kwargs, instance)
        return resp

    # Fallback: wrap the response in our own traced stream for compatibility
    kwargs[LITELLM_ROUTER_INSTANCE_KEY] = instance
    handler_class = LiteLLMAsyncStreamHandler if is_async else LiteLLMStreamHandler
    return make_traced_stream(resp, handler_class(integration, span, args, kwargs))


def traced_completion(func, instance, args, kwargs):
    operation = func.__name__
    integration = litellm._datadog_integration
    model = get_argument_value(args, kwargs, 0, "model", None)
    host = extract_host_tag(kwargs)
    span = integration.trace(
        operation,
        model=model,
        host=host,
        base_url=kwargs.get("base_url", None) or kwargs.get("api_base", None),
        submit_to_llmobs=not integration._has_downstream_openai_span(kwargs, model),
    )
    stream = kwargs.get("stream", False)
    resp = None
    try:
        resp = func(*args, **kwargs)
        if stream:
            return make_traced_stream(resp, LiteLLMStreamHandler(integration, span, args, kwargs))
        return resp
    except Exception:
        span.set_exc_info(*sys.exc_info())
        _usage_attempt_failed(kwargs)
        raise
    finally:
        # streamed spans will be finished separately once the stream generator is exhausted
        if not stream:
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=resp, operation=operation)
            span.finish()


async def traced_acompletion(func, instance, args, kwargs):
    operation = func.__name__
    integration = litellm._datadog_integration
    model = get_argument_value(args, kwargs, 0, "model", None)
    host = extract_host_tag(kwargs)
    span = integration.trace(
        operation,
        model=model,
        host=host,
        base_url=kwargs.get("base_url", None) or kwargs.get("api_base", None),
        submit_to_llmobs=not integration._has_downstream_openai_span(kwargs, model),
    )
    stream = kwargs.get("stream", False)
    resp = None
    try:
        resp = await func(*args, **kwargs)
        if stream:
            return make_traced_stream(resp, LiteLLMAsyncStreamHandler(integration, span, args, kwargs))
        return resp
    except Exception:
        span.set_exc_info(*sys.exc_info())
        _usage_attempt_failed(kwargs)
        raise
    finally:
        # streamed spans will be finished separately once the stream generator is exhausted
        if not stream:
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=resp, operation=operation)
            span.finish()


def traced_router_completion(func, instance, args, kwargs):
    operation = f"router.{func.__name__}"
    integration = litellm._datadog_integration
    model = get_argument_value(args, kwargs, 0, "model", None)
    host = extract_host_tag(kwargs)
    span = integration.trace(
        operation,
        model=model,
        host=host,
        base_url=kwargs.get("base_url", None) or kwargs.get("api_base", None),
        submit_to_llmobs=True,
    )
    _mark_gateway_span(span, kwargs)
    stream = kwargs.get("stream", False)
    resp = None
    try:
        resp = func(*args, **kwargs)
        if stream:
            return _handle_router_stream_response(resp, span, kwargs, instance, integration, args, is_async=False)
        return resp
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        if not stream:
            kwargs[LITELLM_ROUTER_INSTANCE_KEY] = instance
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=resp, operation=operation)
            span.finish()


async def traced_router_acompletion(func, instance, args, kwargs):
    operation = f"router.{func.__name__}"
    integration = litellm._datadog_integration
    model = get_argument_value(args, kwargs, 0, "model", None)
    host = extract_host_tag(kwargs)
    span = integration.trace(
        operation,
        model=model,
        host=host,
        base_url=kwargs.get("base_url", None) or kwargs.get("api_base", None),
        submit_to_llmobs=True,
    )
    _mark_gateway_span(span, kwargs)
    stream = kwargs.get("stream", False)
    resp = None
    try:
        resp = await func(*args, **kwargs)
        if stream:
            return _handle_router_stream_response(resp, span, kwargs, instance, integration, args, is_async=True)
        return resp
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        if not stream:
            kwargs[LITELLM_ROUTER_INSTANCE_KEY] = instance
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=resp, operation=operation)
            span.finish()


def _field(obj, name):
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _positive(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def traced_chunk_creator(func, instance, args, kwargs):
    """Note which token counts a provider's stream chunk reports, and the model it names.

    LiteLLM estimates each side of a stream on its own when no chunk reported it. An Anthropic stream reports its
    input count first, with a placeholder output count; the real output count comes with the final chunk.
    """
    logger = getattr(litellm, "_datadog_usage_metrics_logger", None)
    if logger is not None:
        try:
            chunk = get_argument_value(args, kwargs, 0, "chunk", optional=True)
            usage = _field(chunk, "usage")
            output_reported = _positive(_field(usage, "completion_tokens"))
            if output_reported and getattr(instance, "custom_llm_provider", None) == "anthropic":
                choices = _field(chunk, "choices") or [None]
                output_reported = bool(_field(choices[0], "finish_reason"))
            reasoning = _field(_field(usage, "completion_tokens_details"), "reasoning_tokens")
            model = _field(chunk, "model")
            logger.observe_stream(
                getattr(getattr(instance, "logging_obj", None), "litellm_call_id", None),
                _positive(_field(usage, "prompt_tokens")),
                output_reported,
                model if isinstance(model, str) else None,
                isinstance(reasoning, int),
            )
        except Exception:
            log.debug("LiteLLM usage metrics: failed to inspect a stream chunk", exc_info=True)
    return func(*args, **kwargs)


def traced_anthropic_stream_events(func, instance, args, kwargs):
    """Note which token counts a native Anthropic Messages stream reported, from all its raw events."""
    logger = getattr(litellm, "_datadog_usage_metrics_logger", None)
    if logger is not None:
        try:
            chunks = get_argument_value(args, kwargs, 0, "all_chunks", optional=True) or ()
            logging_obj = get_argument_value(args, kwargs, 1, "litellm_logging_obj", optional=True)
            input_reported, output_reported, model = _usage_metrics.anthropic_stream_usage(chunks)
            logger.observe_stream(getattr(logging_obj, "litellm_call_id", None), input_reported, output_reported, model)
        except Exception:
            log.debug("LiteLLM usage metrics: failed to inspect an Anthropic stream", exc_info=True)
    return func(*args, **kwargs)


def traced_responses_stream_event(func, instance, args, kwargs):
    """Note which token counts a Responses stream event reports. The raw event is read: LiteLLM replaces a missing
    usage with its own estimate before returning it.
    """
    logger = getattr(litellm, "_datadog_usage_metrics_logger", None)
    if logger is not None:
        try:
            chunk = get_argument_value(args, kwargs, 0, "chunk", optional=True)
            input_reported, output_reported, reasoning_reported, model = _usage_metrics.responses_stream_usage(chunk)
            if input_reported or output_reported or model:
                logger.observe_stream(
                    getattr(getattr(instance, "logging_obj", None), "litellm_call_id", None),
                    input_reported,
                    output_reported,
                    model,
                    reasoning_reported,
                )
        except Exception:
            log.debug("LiteLLM usage metrics: failed to inspect a Responses stream event", exc_info=True)
    return func(*args, **kwargs)


_ANTHROPIC_PASSTHROUGH_MODULE = (
    "litellm.proxy.pass_through_endpoints.llm_provider_handlers.anthropic_passthrough_logging_handler"
)
_RESPONSES_STREAM_MODULE = "litellm.responses.streaming_iterator"


# The original class attributes of the stream methods wrapped below. Unpatching restores them as they were: a static
# method put back through its wrapper would come back as a plain function.
_wrapped_stream_methods = {}


def _wrap_stream_method(cls, name, wrapper):
    original = vars(cls).get(name)
    if original is not None and (cls, name) not in _wrapped_stream_methods:
        _wrapped_stream_methods[(cls, name)] = original
        wrap(cls, name, wrapper)


def _unwrap_stream_methods():
    for (cls, name), original in _wrapped_stream_methods.items():
        setattr(cls, name, original)
    _wrapped_stream_methods.clear()


def _wrap_anthropic_stream(module):
    try:
        _wrap_stream_method(
            module.AnthropicPassthroughLoggingHandler,
            "_build_complete_streaming_response",
            traced_anthropic_stream_events,
        )
    except Exception:
        log.debug("LiteLLM usage metrics: cannot observe Anthropic Messages streams", exc_info=True)


def _wrap_responses_stream(module):
    try:
        _wrap_stream_method(module.BaseResponsesAPIStreamingIterator, "_process_chunk", traced_responses_stream_event)
    except Exception:
        log.debug("LiteLLM usage metrics: cannot observe Responses streams", exc_info=True)


# Both are wrapped when LiteLLM imports them: the proxy's pass-through module is imported only when the proxy serves.
_STREAM_MODULE_HOOKS = (
    (_ANTHROPIC_PASSTHROUGH_MODULE, _wrap_anthropic_stream),
    (_RESPONSES_STREAM_MODULE, _wrap_responses_stream),
)


def _usage_attempt_failed(kwargs):
    logger = getattr(litellm, "_datadog_usage_metrics_logger", None)
    if logger is not None:
        logger.attempt_failed(kwargs, sys.exc_info()[1])


def _mark_gateway_span(span, kwargs):
    logger = getattr(litellm, "_datadog_usage_metrics_logger", None)
    if logger is not None and logger.has_request(kwargs.get("litellm_call_id")):
        span._set_attribute(_usage_metrics.RECORDED_PROFILES_TAG, _usage_metrics.GATEWAY_PROFILES)


def _enable_usage_metrics():
    if _usage_metrics_writer.ai_usage is None:
        log.warning("LiteLLM usage metrics are not available in this build of ddtrace")
        return
    exporter = (config.litellm.usage_metrics_exporter or "otlp").strip().lower()
    if exporter not in ("otlp", "dogstatsd"):
        log.warning("Unknown DD_LITELLM_USAGE_METRICS_EXPORTER %r, using otlp", exporter)
        exporter = "otlp"
    tags = frozenset(tag.strip().lower() for tag in (config.litellm.usage_metrics_tags or "").split(",") if tag.strip())
    unknown = tags - _usage_metrics.OPT_IN_TAGS
    if unknown:
        log.warning("Ignoring unknown DD_LITELLM_USAGE_METRICS_TAGS values: %s", ", ".join(sorted(unknown)))
    resource = {}
    if "service" in tags and config.service:
        resource["service.name"] = config.service
    if "host" in tags:
        resource["host.name"] = get_hostname()
    writer = _usage_metrics_writer.UsageMetricsWriter(
        exporter,
        interval=otel_config.exporter.METRICS_METRIC_READER_EXPORT_INTERVAL / 1000.0,
        metrics=list(_usage_metrics.DEFAULT_METRICS),
    )
    logger = _usage_metrics.UsageMetricsLogger(
        writer, tags & _usage_metrics.OPT_IN_TAGS, config.litellm.usage_metrics_client_source, resource
    )
    manager = getattr(litellm, "logging_callback_manager", None)
    if manager is not None:
        manager.add_litellm_callback(logger)
    else:
        litellm.callbacks.append(logger)
    writer.start()
    atexit.register(writer.on_shutdown)
    litellm._datadog_usage_metrics_writer = writer
    litellm._datadog_usage_metrics_logger = logger
    wrap("litellm", "litellm_core_utils.streaming_handler.CustomStreamWrapper.chunk_creator", traced_chunk_creator)
    for module, hook in _STREAM_MODULE_HOOKS:
        ModuleWatchdog.register_module_hook(module, hook)


def _disable_usage_metrics():
    logger = getattr(litellm, "_datadog_usage_metrics_logger", None)
    writer = getattr(litellm, "_datadog_usage_metrics_writer", None)
    if logger is None or writer is None:
        return
    _remove_callback(logger)
    unwrap(litellm.litellm_core_utils.streaming_handler.CustomStreamWrapper, "chunk_creator")
    for module, hook in _STREAM_MODULE_HOOKS:
        ModuleWatchdog.unregister_module_hook(module, hook)
    _unwrap_stream_methods()
    atexit.unregister(writer.on_shutdown)
    writer.stop()
    writer.flush()
    del litellm._datadog_usage_metrics_logger
    del litellm._datadog_usage_metrics_writer


# Every LiteLLM list a callback added to `litellm.callbacks` can end up in: a call copies it into the input, success
# and failure lists, and the proxy into its service list.
_CALLBACK_LISTS = (
    "callbacks",
    "input_callback",
    "success_callback",
    "failure_callback",
    "service_callback",
    "_async_input_callback",
    "_async_success_callback",
    "_async_failure_callback",
)


def _remove_callback(logger):
    """Remove the logger from every LiteLLM callback list. LiteLLM will not add another logger of the same class
    while one is left in a list.
    """
    for name in _CALLBACK_LISTS:
        callbacks = getattr(litellm, name, None)
        if isinstance(callbacks, list) and any(callback is logger for callback in callbacks):
            callbacks[:] = [callback for callback in callbacks if callback is not logger]


def traced_get_llm_provider(func, instance, args, kwargs):
    requested_model = get_argument_value(args, kwargs, 0, "model", None)
    integration = litellm._datadog_integration
    model, custom_llm_provider, dynamic_api_key, api_base = func(*args, **kwargs)
    # store the model name and provider in the integration
    integration._model_map[requested_model] = (model, custom_llm_provider)
    return model, custom_llm_provider, dynamic_api_key, api_base


def patch():
    if getattr(litellm, "_datadog_patch", False):
        return

    litellm._datadog_patch = True

    integration = LiteLLMIntegration(integration_config=config.litellm)
    litellm._datadog_integration = integration

    wrap("litellm", "completion", traced_completion)
    wrap("litellm", "acompletion", traced_acompletion)
    wrap("litellm", "text_completion", traced_completion)
    wrap("litellm", "atext_completion", traced_acompletion)
    wrap("litellm", "get_llm_provider", traced_get_llm_provider)
    wrap("litellm", "main.get_llm_provider", traced_get_llm_provider)
    wrap("litellm", "router.Router.completion", traced_router_completion)
    wrap("litellm", "router.Router.acompletion", traced_router_acompletion)
    wrap("litellm", "router.Router.text_completion", traced_router_completion)
    wrap("litellm", "router.Router.atext_completion", traced_router_acompletion)

    if config.litellm.usage_metrics_enabled:
        _enable_usage_metrics()


def unpatch():
    if not getattr(litellm, "_datadog_patch", False):
        return

    litellm._datadog_patch = False

    _disable_usage_metrics()

    unwrap(litellm, "completion")
    unwrap(litellm, "acompletion")
    unwrap(litellm, "text_completion")
    unwrap(litellm, "atext_completion")
    unwrap(litellm, "get_llm_provider")
    unwrap(litellm.main, "get_llm_provider")
    unwrap(litellm.router.Router, "completion")
    unwrap(litellm.router.Router, "acompletion")
    unwrap(litellm.router.Router, "text_completion")
    unwrap(litellm.router.Router, "atext_completion")
    delattr(litellm, "_datadog_integration")
