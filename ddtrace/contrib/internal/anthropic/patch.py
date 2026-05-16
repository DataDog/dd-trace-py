import sys
from typing import Any
from typing import Callable

import anthropic

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.anthropic._streaming import handle_streamed_response
from ddtrace.contrib.internal.anthropic._streaming import is_streaming_operation
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._integrations import AnthropicIntegration


log = get_logger(__name__)


def get_version() -> str:
    return getattr(anthropic, "__version__", "")


ANTHROPIC_VERSION = parse_version(get_version())


def _supported_versions() -> dict[str, str]:
    return {"anthropic": ">=0.28.0"}


config._add("anthropic", {})


def traced_chat_model_generate(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    integration: AnthropicIntegration = anthropic._datadog_integration
    event = LlmRequestEvent(
        component="anthropic",
        integration_config=config.anthropic,
        service=int_service(None, integration.integration_config),
        resource=f"{instance.__class__.__name__}.{func.__name__}",
        provider="anthropic",
        model=kwargs.get("model", ""),
        llmobs_integration=integration,
        submit_to_llmobs=True,
        request_kwargs=kwargs,
        instance=instance,
    )

    # AIDEV-NOTE: For streaming, dispatch_end_event=False defers the ended event
    # until the stream handler calls ctx.dispatch_ended_event() in finalize_stream().
    # For errors, we must manually dispatch so the span finishes with error info.
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            resp = func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        if is_streaming_operation(resp):
            return handle_streamed_response(integration, resp, args, kwargs, ctx)
        event.response = resp
        ctx.dispatch_ended_event()
        return resp


async def traced_async_chat_model_generate(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    integration: AnthropicIntegration = anthropic._datadog_integration
    event = LlmRequestEvent(
        component="anthropic",
        integration_config=config.anthropic,
        service=int_service(None, integration.integration_config),
        resource=f"{instance.__class__.__name__}.{func.__name__}",
        provider="anthropic",
        model=kwargs.get("model", ""),
        llmobs_integration=integration,
        submit_to_llmobs=True,
        request_kwargs=kwargs,
        instance=instance,
    )

    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            resp = await func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        if is_streaming_operation(resp):
            return handle_streamed_response(integration, resp, args, kwargs, ctx)
        event.response = resp
        ctx.dispatch_ended_event()
        return resp


def patch() -> None:
    if getattr(anthropic, "_datadog_patch", False):
        return

    anthropic._datadog_patch = True

    integration = AnthropicIntegration(integration_config=config.anthropic)
    anthropic._datadog_integration = integration

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

    # AnthropicBedrock / AsyncAnthropicBedrock define their own beta Messages classes in
    # anthropic.lib.bedrock._beta_messages whose `create` attribute is bound to the first-party
    # function at module import time, before patch() runs. The wrap() above on the first-party
    # class does not affect those captured references, so Bedrock callers see no spans unless we
    # also wrap the Bedrock module paths directly.
    try:
        import anthropic.lib.bedrock._beta_messages  # noqa: F401
    except ImportError:
        pass
    else:
        wrap("anthropic", "lib.bedrock._beta_messages.Messages.create", traced_chat_model_generate)
        wrap(
            "anthropic",
            "lib.bedrock._beta_messages.AsyncMessages.create",
            traced_async_chat_model_generate,
        )


def unpatch() -> None:
    if not getattr(anthropic, "_datadog_patch", False):
        return

    anthropic._datadog_patch = False

    unwrap(anthropic.resources.messages.Messages, "create")
    unwrap(anthropic.resources.messages.Messages, "stream")
    unwrap(anthropic.resources.messages.AsyncMessages, "create")
    unwrap(anthropic.resources.messages.AsyncMessages, "stream")

    if ANTHROPIC_VERSION >= (0, 37):
        unwrap(anthropic.resources.beta.messages.messages.Messages, "create")
        unwrap(anthropic.resources.beta.messages.messages.Messages, "stream")
        unwrap(anthropic.resources.beta.messages.messages.AsyncMessages, "create")
        unwrap(anthropic.resources.beta.messages.messages.AsyncMessages, "stream")

    try:
        import anthropic.lib.bedrock._beta_messages as _bedrock_beta_messages
    except ImportError:
        pass
    else:
        unwrap(_bedrock_beta_messages.Messages, "create")
        unwrap(_bedrock_beta_messages.AsyncMessages, "create")

    delattr(anthropic, "_datadog_integration")
