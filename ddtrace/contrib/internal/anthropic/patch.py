import inspect
import sys
from typing import Any
from typing import Callable

import anthropic

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.anthropic._constants import COMPONENT
from ddtrace.contrib.internal.anthropic._constants import TAG_MODEL
from ddtrace.contrib.internal.anthropic._streaming import handle_streamed_response
from ddtrace.contrib.internal.anthropic._streaming import is_streaming_operation
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal import integration_hooks
from ddtrace.internal._exceptions import DDBlockException
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.version import parse_version


log = get_logger(__name__)


def get_version() -> str:
    return getattr(anthropic, "__version__", "")


ANTHROPIC_VERSION = parse_version(get_version())


def _supported_versions() -> dict[str, str]:
    return {"anthropic": ">=0.28.0"}


config._add("anthropic", {})


def _is_wrapped_response(resp: Any) -> bool:
    # messages.with_raw_response and messages.with_streaming_response return an
    # APIResponse/LegacyAPIResponse wrapper; the parsed Message sits behind parse().
    return resp is not None and hasattr(resp, "parse") and hasattr(resp, "http_response")


def _parse_wrapped_response(resp: Any) -> Any:
    """Return the parsed Message behind a raw or streaming response wrapper.

    parse() caches its result on the wrapper, so the caller's own parse() returns
    the same object. Any other response, or a failed parse, is returned unchanged.
    A failed body read propagates: the stream is spent by then, so the caller would
    only get StreamAlreadyConsumed in place of the transport error. LegacyAPIResponse
    has no read() because its body is already in memory.
    """
    if not _is_wrapped_response(resp):
        return resp
    if hasattr(resp, "read"):
        resp.read()
    try:
        return resp.parse()
    except Exception:
        log.debug("Failed to parse anthropic response wrapper", exc_info=True)
        return resp


async def _async_parse_wrapped_response(resp: Any) -> Any:
    """Async counterpart of _parse_wrapped_response.

    AsyncAPIResponse.parse() is a coroutine, while LegacyAPIResponse.parse() stays
    synchronous on the async client, so the result is awaited only when it is awaitable.
    """
    if not _is_wrapped_response(resp):
        return resp
    if hasattr(resp, "read"):
        body = resp.read()
        if inspect.isawaitable(body):
            await body
    try:
        parsed = resp.parse()
        if inspect.isawaitable(parsed):
            parsed = await parsed
        return parsed
    except Exception:
        log.debug("Failed to parse anthropic response wrapper", exc_info=True)
        return resp


def _request_event(func: Callable[..., Any], instance: Any, kwargs: Any) -> LlmRequestEvent:
    model = kwargs.get("model", "")
    return LlmRequestEvent(
        component=COMPONENT,
        integration_config=config.anthropic,
        service=int_service(None, config.anthropic),
        resource=f"{instance.__class__.__name__}.{func.__name__}",
        provider="anthropic",
        model=model,
        tags={TAG_MODEL: model},
        submit_to_llmobs=True,
        request_kwargs=kwargs,
        instance=instance,
    )


def traced_chat_model_generate(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    event = _request_event(func, instance, kwargs)

    # For streaming, dispatch_end_event=False defers the ended event
    # until the stream handler calls ctx.dispatch_ended_event() in finalize_stream().
    # For errors, we must manually dispatch so the span finishes with error info.
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            core.dispatch("anthropic.messages.create.before", (kwargs,), allow_raise=True)
            resp = func(*args, **kwargs)
        except (DDBlockException, Exception):
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        if is_streaming_operation(resp):
            return handle_streamed_response(resp, args, kwargs, ctx)
        try:
            parsed_resp = _parse_wrapped_response(resp) if not kwargs.get("stream") else resp
        except BaseException:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        # Attach the response to the event before the after-hook so that an AI
        # Guard block raised by that hook still records the model output in
        # LLMObs, even though the block errors the span (APPSEC-68147).
        event.response = parsed_resp
        try:
            core.dispatch("anthropic.messages.create.after", (kwargs, parsed_resp), allow_raise=True)
        except (DDBlockException, Exception):
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        ctx.dispatch_ended_event()
        return resp


async def traced_async_chat_model_generate(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    event = _request_event(func, instance, kwargs)

    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            core.dispatch("anthropic.messages.create.before", (kwargs,), allow_raise=True)
            resp = await func(*args, **kwargs)
        except (DDBlockException, Exception):
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        if is_streaming_operation(resp):
            return handle_streamed_response(resp, args, kwargs, ctx)
        try:
            parsed_resp = await _async_parse_wrapped_response(resp) if not kwargs.get("stream") else resp
        except BaseException:
            # BaseException, so a cancelled body read still finishes the span.
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        # Attach the response to the event before the after-hook so that an AI
        # Guard block raised by that hook still records the model output in
        # LLMObs, even though the block errors the span (APPSEC-68147).
        event.response = parsed_resp
        try:
            core.dispatch("anthropic.messages.create.after", (kwargs, parsed_resp), allow_raise=True)
        except (DDBlockException, Exception):
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        ctx.dispatch_ended_event()
        return resp


def patch() -> None:
    if getattr(anthropic, "_datadog_patch", False):
        return

    anthropic._datadog_patch = True

    # AI Guard mirrors this wrap-target list in
    # ddtrace/appsec/_ai_guard/_listener.py::_install_anthropic_wrappers to
    # install its outermost streaming buffer. If you add/rename a target or
    # change the >= (0, 37) beta gate below, update that list too or the new
    # surface goes unbuffered for stream-response evaluation.
    wrap("anthropic", "resources.messages.Messages.create", traced_chat_model_generate)
    wrap("anthropic", "resources.messages.Messages.stream", traced_chat_model_generate)
    wrap("anthropic", "resources.messages.AsyncMessages.create", traced_async_chat_model_generate)
    # AsyncMessages.stream is a sync function
    wrap("anthropic", "resources.messages.AsyncMessages.stream", traced_chat_model_generate)

    if ANTHROPIC_VERSION >= (0, 37):
        wrap("anthropic", "resources.beta.messages.messages.Messages.create", traced_chat_model_generate)
        wrap("anthropic", "resources.beta.messages.messages.Messages.stream", traced_chat_model_generate)
        wrap(
            "anthropic",
            "resources.beta.messages.messages.AsyncMessages.create",
            traced_async_chat_model_generate,
        )
        wrap("anthropic", "resources.beta.messages.messages.AsyncMessages.stream", traced_chat_model_generate)

    # Notify AI Guard (and any other plugin) that wrapping is complete so they
    # can install their own outermost wrappers on the same targets.
    core.dispatch("anthropic.patch", tuple())
    integration_hooks.on_patch("anthropic")


def unpatch() -> None:
    if not getattr(anthropic, "_datadog_patch", False):
        return

    anthropic._datadog_patch = False
    # Notify AI Guard first so it can peel its outermost wrappers before the
    # contrib's own unwrap calls restore the layer below.
    core.dispatch("anthropic.unpatch", tuple())
    integration_hooks.on_unpatch("anthropic")

    unwrap(anthropic.resources.messages.Messages, "create")
    unwrap(anthropic.resources.messages.Messages, "stream")
    unwrap(anthropic.resources.messages.AsyncMessages, "create")
    unwrap(anthropic.resources.messages.AsyncMessages, "stream")

    if ANTHROPIC_VERSION >= (0, 37):
        unwrap(anthropic.resources.beta.messages.messages.Messages, "create")
        unwrap(anthropic.resources.beta.messages.messages.Messages, "stream")
        unwrap(anthropic.resources.beta.messages.messages.AsyncMessages, "create")
        unwrap(anthropic.resources.beta.messages.messages.AsyncMessages, "stream")
