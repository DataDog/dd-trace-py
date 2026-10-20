import sys
import time
import weakref

import litellm

from ddtrace import config
from ddtrace._trace.processor import SpanProcessor
from ddtrace.contrib.internal.litellm import _usage_metrics
from ddtrace.contrib.internal.litellm import _usage_metrics_writer
from ddtrace.contrib.internal.litellm.utils import LiteLLMAsyncStreamHandler
from ddtrace.contrib.internal.litellm.utils import LiteLLMStreamHandler
from ddtrace.contrib.internal.litellm.utils import extract_host_tag
from ddtrace.contrib.internal.stream_handler import make_traced_stream
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.settings._opentelemetry import otel_config
from ddtrace.internal.telemetry import get_config as _get_config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.llmobs._constants import LITELLM_ROUTER_INSTANCE_KEY
from ddtrace.llmobs._integrations import LiteLLMIntegration


log = get_logger(__name__)

config._add(
    "litellm",
    {
        "usage_metrics_enabled": _get_config("DD_LITELLM_USAGE_METRICS_ENABLED", default=False, modifier=asbool),
        "usage_metrics_tags": _get_config("DD_LITELLM_USAGE_METRICS_TAGS"),
        "usage_metrics_exporter": _get_config("DD_LITELLM_USAGE_METRICS_EXPORTER", default="otlp"),
        "usage_metrics_client_source": _get_config("DD_LITELLM_USAGE_METRICS_CLIENT_SOURCE"),
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


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


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
            prompt, completion = _field(usage, "prompt_tokens"), _field(usage, "completion_tokens")
            # A usage of zeros is LiteLLM's placeholder for none; a zero next to a positive count is the provider's.
            real = (_count(prompt) and prompt > 0) or (_count(completion) and completion > 0)
            input_reported = real and _count(prompt)
            output_reported = real and _count(completion)
            if output_reported and getattr(instance, "custom_llm_provider", None) == "anthropic":
                choices = _field(chunk, "choices") or [None]
                output_reported = bool(_field(choices[0], "finish_reason"))
            reasoning = _field(_field(usage, "completion_tokens_details"), "reasoning_tokens")
            model = _field(chunk, "model")
            if not isinstance(model, str) or not model:
                model = _served_model(getattr(instance, "completion_stream", None))
            logger.observe_stream(
                getattr(getattr(instance, "logging_obj", None), "litellm_call_id", None),
                input_reported,
                output_reported,
                model if isinstance(model, str) else None,
                isinstance(reasoning, int),
            )
        except Exception:
            log.debug("LiteLLM usage metrics: failed to inspect a stream chunk", exc_info=True)
    return func(*args, **kwargs)


# The model each Anthropic chat stream's message_start event names, by LiteLLM's stream iterator. LiteLLM before 1.10x
# builds that stream's chunks without it.
_anthropic_served_models: "weakref.WeakKeyDictionary[object, str]" = weakref.WeakKeyDictionary()


def _served_model(stream):
    try:
        return _anthropic_served_models.get(stream)
    except TypeError:
        # A stream that takes no weak reference (a string, a list) is not an Anthropic stream iterator.
        return None


def traced_anthropic_chunk_parser(func, instance, args, kwargs):
    """Note the model an Anthropic chat stream's message_start event names."""
    try:
        chunk = get_argument_value(args, kwargs, 0, "chunk", optional=True)
        if _field(chunk, "type") == "message_start":
            model = _field(_field(chunk, "message"), "model")
            if isinstance(model, str) and model:
                _anthropic_served_models[instance] = model
    except Exception:
        log.debug("LiteLLM usage metrics: failed to inspect an Anthropic stream event", exc_info=True)
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
_ANTHROPIC_CHAT_MODULE = "litellm.llms.anthropic.chat.handler"


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


def _wrap_anthropic_chat_stream(module):
    try:
        _wrap_stream_method(module.ModelResponseIterator, "chunk_parser", traced_anthropic_chunk_parser)
    except Exception:
        log.debug("LiteLLM usage metrics: cannot observe Anthropic chat streams", exc_info=True)


def _wrap_responses_stream(module):
    try:
        _wrap_stream_method(module.BaseResponsesAPIStreamingIterator, "_process_chunk", traced_responses_stream_event)
    except Exception:
        log.debug("LiteLLM usage metrics: cannot observe Responses streams", exc_info=True)


# Each is wrapped when LiteLLM imports it: the proxy's pass-through module is imported only when the proxy serves.
_STREAM_MODULE_HOOKS = (
    (_ANTHROPIC_PASSTHROUGH_MODULE, _wrap_anthropic_stream),
    (_RESPONSES_STREAM_MODULE, _wrap_responses_stream),
    (_ANTHROPIC_CHAT_MODULE, _wrap_anthropic_chat_stream),
)


def _usage_attempt_failed(kwargs):
    logger = getattr(litellm, "_datadog_usage_metrics_logger", None)
    if logger is not None:
        logger.attempt_failed(kwargs, sys.exc_info()[1])


def _mark_gateway_span(span, kwargs):
    logger = getattr(litellm, "_datadog_usage_metrics_logger", None)
    if logger is None:
        return
    # Every proxy request gets a gateway observation. Proxies that assign the call id after async_pre_call_hook
    # (LiteLLM 1.6x) pass the router no call id yet, and a gateway cache hit makes no provider attempt that could
    # mark this span later, so a proxy request is recognized by the request data the proxy adds.
    if logger.has_request(kwargs.get("litellm_call_id")) or kwargs.get("proxy_server_request"):
        span._set_attribute(_usage_metrics.RECORDED_PROFILES_TAG, _usage_metrics.GATEWAY_PROFILES)


# A signal handler chained in cannot be removed, so the exit-signal flush is installed once and flushes whichever writer
# is current.
_exit_signal_flush_installed = False


def _export_interval_seconds() -> float:
    interval_ms = otel_config.exporter.METRICS_METRIC_READER_EXPORT_INTERVAL
    if interval_ms <= 0:
        default_ms = otel_config.exporter.DEFAULT_METRICS_METRIC_READER_EXPORT_INTERVAL
        log.warning(
            "Ignoring OTEL_METRIC_EXPORT_INTERVAL=%s, which is not positive; using %s ms", interval_ms, default_ms
        )
        interval_ms = default_ms
    return float(interval_ms) / 1000.0


def _flush_usage_metrics_on_exit_signal(deadline=None):
    writer = getattr(litellm, "_datadog_usage_metrics_writer", None)
    if writer is not None:
        writer.on_shutdown(deadline)


class _ExitSignalFlush(SpanProcessor):
    """Flushes the usage metrics when the tracer shuts down. Off the main thread no signal handler can be installed,
    but the tracer's own exit-signal handler, installed when it started, shuts every span processor down.
    """

    def on_span_start(self, span):
        pass

    def on_span_finish(self, span):
        pass

    def shutdown(self, timeout):
        _flush_usage_metrics_on_exit_signal(None if timeout is None else time.monotonic() + max(timeout, 0.0))


def _enable_usage_metrics():
    global _exit_signal_flush_installed
    if _usage_metrics_writer.ai_usage is None:
        log.warning("LiteLLM usage metrics are not available in this build of ddtrace")
        return
    exporter = (config.litellm.usage_metrics_exporter or "otlp").strip().lower()
    if exporter not in ("otlp", "dogstatsd"):
        log.warning("Unknown DD_LITELLM_USAGE_METRICS_EXPORTER %r, using otlp", exporter)
        exporter = "otlp"
    if exporter == "otlp" and _usage_metrics_writer.otlp_endpoint_is_grpc():
        log.warning(
            "LiteLLM usage metrics are not enabled: they are sent over OTLP/HTTP, and the OTLP metrics endpoint is "
            "configured for gRPC. Set OTEL_EXPORTER_OTLP_METRICS_PROTOCOL=http/protobuf with an OTLP/HTTP "
            "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT."
        )
        return
    tags = frozenset(tag.strip().lower() for tag in (config.litellm.usage_metrics_tags or "").split(",") if tag.strip())
    unknown = tags - _usage_metrics.OPT_IN_TAGS
    if unknown:
        log.warning("Ignoring unknown DD_LITELLM_USAGE_METRICS_TAGS values: %s", ", ".join(sorted(unknown)))
    resource = {}
    if "service" in tags and config.service:
        resource["service.name"] = config.service
    if "host" in tags:
        resource["host.name"] = get_hostname()
    try:
        writer = _usage_metrics_writer.UsageMetricsWriter(
            exporter,
            interval=_export_interval_seconds(),
            metrics=list(_usage_metrics.DEFAULT_METRICS),
        )
        writer.start()
    except Exception as e:
        # An endpoint the exporter cannot use, such as an ftp:// OTLP endpoint or a tcp:// DogStatsD URL, or a thread
        # that cannot start, leaves the metrics off without failing the rest of the integration.
        # The type only: an exporter's error can quote its endpoint, credentials included.
        log.warning("LiteLLM usage metrics are not enabled: the exporter could not start (%s)", type(e).__name__)
        return
    logger = _usage_metrics.UsageMetricsLogger(
        writer, tags & _usage_metrics.OPT_IN_TAGS, config.litellm.usage_metrics_client_source, resource
    )
    # LiteLLM registers one logger of a class: a usage metrics logger left in its lists would make it ignore this one.
    _remove_usage_metrics_loggers()
    manager = getattr(litellm, "logging_callback_manager", None)
    if manager is not None:
        manager.add_litellm_callback(logger)
    if not any(callback is logger for callback in litellm.callbacks):
        litellm.callbacks.append(logger)
    atexit.register(writer.on_shutdown)
    if not _exit_signal_flush_installed:
        # Termination through ddtrace's signal handlers re-raises the signal, which skips atexit. A handler installed
        # now runs before the native runtime's, which the OTLP export needs.
        if atexit.register_on_exit_signal(_flush_usage_metrics_on_exit_signal) is None:
            _ExitSignalFlush().register()
        _exit_signal_flush_installed = True
    forksafe.register(logger.reset)
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
    _remove_usage_metrics_loggers()
    unwrap(litellm.litellm_core_utils.streaming_handler.CustomStreamWrapper, "chunk_creator")
    for module, hook in _STREAM_MODULE_HOOKS:
        ModuleWatchdog.unregister_module_hook(module, hook)
    _unwrap_stream_methods()
    atexit.unregister(writer.on_shutdown)
    forksafe.unregister(logger.reset)
    writer.close()
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


def _remove_usage_metrics_loggers():
    """Remove every usage metrics logger from every LiteLLM callback list."""
    for name in _CALLBACK_LISTS:
        callbacks = getattr(litellm, name, None)
        if isinstance(callbacks, list) and any(isinstance(c, _usage_metrics.UsageMetricsLogger) for c in callbacks):
            callbacks[:] = [c for c in callbacks if not isinstance(c, _usage_metrics.UsageMetricsLogger)]


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
