import asyncio
import sys
import time
from typing import Optional
import weakref

import litellm

from ddtrace import config
from ddtrace._trace.processor import SpanProcessor
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.litellm import _usage_metrics
from ddtrace.contrib.internal.litellm._constants import COMPONENT
from ddtrace.contrib.internal.litellm._constants import ROUTER_INSTANCE_KEY
from ddtrace.contrib.internal.litellm._constants import TAG_HOST
from ddtrace.contrib.internal.litellm._constants import TAG_MODEL
from ddtrace.contrib.internal.litellm._constants import model_map
from ddtrace.contrib.internal.litellm.utils import BaseLiteLLMStreamHandler
from ddtrace.contrib.internal.litellm.utils import LiteLLMAsyncStreamHandler
from ddtrace.contrib.internal.litellm.utils import LiteLLMStreamHandler
from ddtrace.contrib.internal.litellm.utils import extract_host_tag
from ddtrace.contrib.internal.litellm.utils import has_downstream_openai_span
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal import atexit
from ddtrace.internal import core
from ddtrace.internal import forksafe
from ddtrace.internal import integration_hooks
from ddtrace.internal.ai_usage import _writer as _usage_metrics_writer
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.settings._opentelemetry import otel_config
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.telemetry import get_config as _get_config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.streaming import make_traced_stream
from ddtrace.trace import tracer


log = get_logger(__name__)

config._add(
    "litellm",
    {
        "usage_metrics_enabled": _get_config("DD_LITELLM_USAGE_METRICS_ENABLED", default=False, modifier=asbool),
        "usage_metrics_tags": _get_config("DD_LITELLM_USAGE_METRICS_TAGS"),
        "usage_metrics_exporter": _get_config("DD_LITELLM_USAGE_METRICS_EXPORTER", default="dogstatsd"),
        "usage_metrics_client_source": _get_config("DD_LITELLM_USAGE_METRICS_CLIENT_SOURCE"),
    },
)


def get_version() -> str:
    version_module = getattr(litellm, "_version", None)
    return getattr(version_module, "version", "")


def _supported_versions() -> dict[str, str]:
    return {"litellm": "*"}


def _request_event(operation, args, kwargs, router=None):
    model = get_argument_value(args, kwargs, 0, "model", None)
    host = extract_host_tag(kwargs)
    tags = {}
    if model is not None:
        tags[TAG_MODEL] = model
    if host is not None:
        tags[TAG_HOST] = host
    # Copy so the router instance never reaches the wrapped call.
    request_kwargs = dict(kwargs)
    if router is not None:
        request_kwargs[ROUTER_INSTANCE_KEY] = router
    return LlmRequestEvent(
        component=COMPONENT,
        integration_config=config.litellm,
        service=int_service(None, config.litellm),
        resource=operation,
        # The provider is only known once litellm resolves the model.
        provider="",
        model=model,
        tags=tags,
        # Router spans are always submitted; plain calls defer to the OpenAI integration's span when there is one.
        submit_to_llmobs=router is not None or not has_downstream_openai_span(kwargs, model),
        request_args=args,
        request_kwargs=request_kwargs,
        operation=operation,
    )


def _handle_router_stream_response(resp, ctx, is_async=False):
    """
    Handle router streaming responses with fallback for different wrapper types.

    In litellm>=1.74.15, router streaming responses may be wrapped in FallbackStreamWrapper
    (for mid-stream fallback support) or other types that don't expose the .handler attribute.
    """
    handler = getattr(resp, "handler", None)
    if isinstance(handler, BaseLiteLLMStreamHandler):
        handler.add_context(ctx)
        return resp

    # Fallback: wrap the response in our own traced stream for compatibility
    handler_class = LiteLLMAsyncStreamHandler if is_async else LiteLLMStreamHandler
    return make_traced_stream(resp, handler_class(ctx))


def traced_completion(func, instance, args, kwargs):
    event = _request_event(func.__name__, args, kwargs)
    if not kwargs.get("stream", False):
        with core.context_with_event(event):
            try:
                event.response = func(*args, **kwargs)
            except Exception:
                _usage_attempt_failed(kwargs)
                raise
            return event.response
    # The stream handler dispatches the ended event once the stream is consumed or closed.
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            resp = func(*args, **kwargs)
        except Exception:
            _usage_attempt_failed(kwargs)
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        return make_traced_stream(resp, LiteLLMStreamHandler(ctx))


async def traced_acompletion(func, instance, args, kwargs):
    event = _request_event(func.__name__, args, kwargs)
    if not kwargs.get("stream", False):
        with core.context_with_event(event):
            try:
                event.response = await func(*args, **kwargs)
            except Exception:
                _usage_attempt_failed(kwargs)
                raise
            return event.response
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            resp = await func(*args, **kwargs)
        except Exception:
            _usage_attempt_failed(kwargs)
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        return make_traced_stream(resp, LiteLLMAsyncStreamHandler(ctx))


def traced_router_completion(func, instance, args, kwargs):
    event = _request_event(f"router.{func.__name__}", args, kwargs, router=instance)
    if not kwargs.get("stream", False):
        with core.context_with_event(event) as ctx:
            _mark_gateway_context(ctx, kwargs)
            event.response = func(*args, **kwargs)
            return event.response
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        _mark_gateway_context(ctx, kwargs)
        try:
            resp = func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        return _handle_router_stream_response(resp, ctx, is_async=False)


async def traced_router_acompletion(func, instance, args, kwargs):
    event = _request_event(f"router.{func.__name__}", args, kwargs, router=instance)
    if not kwargs.get("stream", False):
        with core.context_with_event(event) as ctx:
            _mark_gateway_context(ctx, kwargs)
            event.response = await func(*args, **kwargs)
            return event.response
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        _mark_gateway_context(ctx, kwargs)
        try:
            resp = await func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        return _handle_router_stream_response(resp, ctx, is_async=True)


def _field(obj, name):
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def traced_chunk_creator(func, instance, args, kwargs):
    """Note which token counts a provider's stream chunk reports, and the model and service tier it names.

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
            choices = _field(chunk, "choices") or [None]
            # The provider ended the response, every choice of it: a client that leaves after this chunk does not
            # fail the attempt.
            finished = _stream_finished(instance, choices)
            if output_reported and getattr(instance, "custom_llm_provider", None) == "anthropic":
                output_reported = bool(_field(choices[0], "finish_reason"))
            reasoning = _field(_field(usage, "completion_tokens_details"), "reasoning_tokens")
            model = _field(chunk, "model")
            # OpenAI's chunks name the tier at their top level; an Anthropic stream names it in message_start only.
            tier = _field(chunk, "service_tier")
            if not isinstance(model, str) or not model or not isinstance(tier, str) or not tier:
                start_model, start_tier = _anthropic_stream_start(getattr(instance, "completion_stream", None))
                if not isinstance(model, str) or not model:
                    model = start_model
                if not isinstance(tier, str) or not tier:
                    tier = start_tier
            logger.observe_stream(
                getattr(getattr(instance, "logging_obj", None), "litellm_call_id", None),
                input_reported,
                output_reported,
                model if isinstance(model, str) else None,
                isinstance(reasoning, int),
                tier if isinstance(tier, str) else None,
                completed=finished,
            )
        except Exception:
            log.debug("LiteLLM usage metrics: failed to inspect a stream chunk", exc_info=True)
    return func(*args, **kwargs)


# The choice indexes each chat or text stream has shown, and those that finished, by LiteLLM's stream wrapper. With
# n > 1, choices finish in different chunks.
_stream_choices: "weakref.WeakKeyDictionary[object, tuple[set[int], set[int]]]" = weakref.WeakKeyDictionary()


def _requested_choices(stream) -> Optional[int]:
    """The number of choices the request asked for (n), from the parameters LiteLLM sent the provider."""
    n = _field(getattr(getattr(stream, "logging_obj", None), "optional_params", None), "n")
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else None


def _stream_finished(stream, choices) -> bool:
    """Whether every choice of the stream has finished: every requested one when the request's n is known, else every
    one the stream has shown so far, these chunk choices included.
    """
    try:
        seen, finished = _stream_choices.setdefault(stream, (set(), set()))
    except TypeError:
        # A stream that takes no weak reference: only this chunk's choices are known.
        seen, finished = set(), set()
    for position, choice in enumerate(choices):
        index = _field(choice, "index")
        index = index if isinstance(index, int) else position
        seen.add(index)
        if _field(choice, "finish_reason"):
            finished.add(index)
    requested = _requested_choices(stream)
    if requested is not None:
        # A choice can finish before another's first chunk arrives.
        seen = seen | set(range(requested))
    return bool(finished) and seen <= finished


# The model and service tier each Anthropic chat stream's message_start event names, by LiteLLM's stream iterator.
# LiteLLM before 1.10x builds that stream's chunks without the model, and no version passes the tier on.
_anthropic_stream_starts: "weakref.WeakKeyDictionary[object, tuple[Optional[str], Optional[str]]]" = (
    weakref.WeakKeyDictionary()
)


def _anthropic_stream_start(stream) -> "tuple[Optional[str], Optional[str]]":
    try:
        return _anthropic_stream_starts.get(stream, (None, None))
    except TypeError:
        # A stream that takes no weak reference (a string, a list) is not an Anthropic stream iterator.
        return None, None


def traced_anthropic_chunk_parser(func, instance, args, kwargs):
    """Note the model and service tier an Anthropic chat stream's message_start event names."""
    try:
        chunk = get_argument_value(args, kwargs, 0, "chunk", optional=True)
        if _field(chunk, "type") == "message_start":
            message = _field(chunk, "message")
            model = _field(message, "model")
            tier = _usage_metrics.valid_service_tier(_field(_field(message, "usage"), "service_tier"))
            model = model if isinstance(model, str) and model else None
            if model or tier:
                _anthropic_stream_starts[instance] = (model, tier)
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
            input_reported, output_reported, model, tier, completed = _usage_metrics.anthropic_stream_usage(chunks)
            logger.observe_stream(
                getattr(logging_obj, "litellm_call_id", None),
                input_reported,
                output_reported,
                model,
                service_tier=tier,
                completed=completed,
            )
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
            input_reported, output_reported, reasoning_reported, model, completed = (
                _usage_metrics.responses_stream_usage(chunk)
            )
            if input_reported or output_reported or model or completed:
                logger.observe_stream(
                    getattr(getattr(instance, "logging_obj", None), "litellm_call_id", None),
                    input_reported,
                    output_reported,
                    model,
                    reasoning_reported,
                    completed=completed,
                )
        except Exception:
            log.debug("LiteLLM usage metrics: failed to inspect a Responses stream event", exc_info=True)
    return func(*args, **kwargs)


_ANTHROPIC_PASSTHROUGH_MODULE = (
    "litellm.proxy.pass_through_endpoints.llm_provider_handlers.anthropic_passthrough_logging_handler"
)
_RESPONSES_STREAM_MODULE = "litellm.responses.streaming_iterator"
_ANTHROPIC_CHAT_MODULE = "litellm.llms.anthropic.chat.handler"
_PROXY_SERVER_MODULE = "litellm.proxy.proxy_server"
_PROXY_CALLBACKS_MODULE = "litellm.proxy.common_utils.callback_utils"
_INITIALIZE_CALLBACKS = "initialize_callbacks_on_proxy"


async def traced_proxy_shutdown_event(func, instance, args, kwargs):
    """Flush the usage metrics once the proxy has shut down, as its last callbacks have run.

    A gunicorn UvicornWorker (litellm --run_gunicorn) resets SIGTERM to its default action after the fork, and uvicorn
    re-raises the signal once it has shut down: the worker dies by the signal, so neither the exit-signal flush nor
    atexit runs, and everything recorded since the last flush would be lost.
    """
    try:
        return await func(*args, **kwargs)
    finally:
        writer = getattr(litellm, "_datadog_usage_metrics_writer", None)
        if writer is not None:
            try:
                # Off the event loop: an export can take up to the shutdown time.
                await asyncio.to_thread(writer.on_exit)
            except Exception:
                log.debug("LiteLLM usage metrics: failed to flush at proxy shutdown", exc_info=True)


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
        # LiteLLM 1.106 renamed the builder and kept the old name as an alias of it; callers use either name.
        for name in ("build_complete_streaming_response", "_build_complete_streaming_response"):
            _wrap_stream_method(module.AnthropicPassthroughLoggingHandler, name, traced_anthropic_stream_events)
    except Exception:
        log.debug("LiteLLM usage metrics: cannot observe Anthropic Messages streams", exc_info=True)


def _wrap_anthropic_chat_stream(module):
    try:
        _wrap_stream_method(module.ModelResponseIterator, "chunk_parser", traced_anthropic_chunk_parser)
    except Exception:
        log.debug("LiteLLM usage metrics: cannot observe Anthropic chat streams", exc_info=True)


def traced_initialize_callbacks_on_proxy(func, instance, args, kwargs):
    """Attach the usage metrics logger again once the proxy has set up its configured callbacks: a single callback
    given as a string, rather than a list, replaces litellm.callbacks, which would drop the logger.
    """
    try:
        return func(*args, **kwargs)
    finally:
        logger = getattr(litellm, "_datadog_usage_metrics_logger", None)
        if logger is not None:
            try:
                _attach_usage_metrics_logger(logger)
            except Exception:
                log.debug(
                    "LiteLLM usage metrics: failed to attach the logger after the proxy's callbacks", exc_info=True
                )


def _wrap_proxy_shutdown(module):
    try:
        _wrap_stream_method(module, "proxy_shutdown_event", traced_proxy_shutdown_event)
    except Exception:
        log.debug("LiteLLM usage metrics: cannot flush at proxy shutdown", exc_info=True)
    _wrap_initialize_callbacks(module)


def _wrap_initialize_callbacks(module):
    """Wrap the proxy's callback setup where it is defined and where the proxy server imported it by name, unless
    that name already refers to the wrapped definition: the proxy server imported after patching. Unpatching then
    restores that name to the original too.
    """
    try:
        defining = sys.modules.get(_PROXY_CALLBACKS_MODULE)
        if module is not defining and defining is not None:
            if vars(module).get(_INITIALIZE_CALLBACKS) is vars(defining).get(_INITIALIZE_CALLBACKS):
                original = _wrapped_stream_methods.get((defining, _INITIALIZE_CALLBACKS))
                if original is not None:
                    _wrapped_stream_methods.setdefault((module, _INITIALIZE_CALLBACKS), original)
                return
        _wrap_stream_method(module, _INITIALIZE_CALLBACKS, traced_initialize_callbacks_on_proxy)
    except Exception:
        log.debug("LiteLLM usage metrics: cannot keep the logger through the proxy's callback setup", exc_info=True)


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
    (_PROXY_CALLBACKS_MODULE, _wrap_initialize_callbacks),
    (_PROXY_SERVER_MODULE, _wrap_proxy_shutdown),
)


def _usage_attempt_failed(kwargs):
    logger = getattr(litellm, "_datadog_usage_metrics_logger", None)
    if logger is not None:
        logger.attempt_failed(kwargs, sys.exc_info()[1])


def _mark_gateway_context(ctx, kwargs):
    """Mark the Router span the request event's context started as a gateway request."""
    if getattr(litellm, "_datadog_usage_metrics_logger", None) is not None:
        _mark_gateway_span(span_from_context(ctx), kwargs)


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
        # Every Tracer shuts the global span processors down. Only the global tracer's shutdown, which its exit-signal
        # handler runs, means the process is exiting; an application's own tracer shutting down must not stop the
        # writer's retries.
        if not tracer._shutdown_lock.locked():
            return
        _flush_usage_metrics_on_exit_signal(None if timeout is None else time.monotonic() + max(timeout, 0.0))


def _enable_usage_metrics():
    global _exit_signal_flush_installed
    if _usage_metrics_writer.ai_usage is None:
        log.warning("LiteLLM usage metrics are not available in this build of ddtrace")
        return
    # DogStatsD by default: the Agent stores its counters as rate metrics, as every other submitter of these metric
    # names does, while Datadog stores OTLP delta sums as count metrics. OTLP is for other backends, and only when
    # asked for. An unknown value is never replaced by another exporter.
    exporter = (config.litellm.usage_metrics_exporter or "dogstatsd").strip().lower()
    if exporter not in ("otlp", "dogstatsd"):
        log.warning(
            "LiteLLM usage metrics are not enabled: unknown DD_LITELLM_USAGE_METRICS_EXPORTER %r (expected dogstatsd "
            "or otlp)",
            exporter,
        )
        return
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
            scope_name=_usage_metrics.SCOPE_NAME,
            integration_name="litellm",
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
    _attach_usage_metrics_logger(logger)
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


def _attach_usage_metrics_logger(logger):
    """Add the logger to litellm.callbacks unless it is there already."""
    if any(callback is logger for callback in litellm.callbacks):
        return
    manager = getattr(litellm, "logging_callback_manager", None)
    if manager is not None:
        manager.add_litellm_callback(logger)
    if not any(callback is logger for callback in litellm.callbacks):
        litellm.callbacks.append(logger)


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
    model, custom_llm_provider, dynamic_api_key, api_base = func(*args, **kwargs)
    model_map[requested_model] = (model, custom_llm_provider)
    return model, custom_llm_provider, dynamic_api_key, api_base


def patch():
    if getattr(litellm, "_datadog_patch", False):
        return

    litellm._datadog_patch = True

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

    # Let products (LLMObs) attach their own LlmEvents subscribers without this
    # module importing them.
    integration_hooks.on_patch(COMPONENT)

    if config.litellm.usage_metrics_enabled:
        _enable_usage_metrics()


def unpatch():
    if not getattr(litellm, "_datadog_patch", False):
        return

    litellm._datadog_patch = False

    integration_hooks.on_unpatch(COMPONENT)

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
