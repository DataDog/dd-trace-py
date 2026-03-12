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


def _create_llm_event(integration, instance, func, kwargs):
    """Create an LlmRequestEvent for an Anthropic call."""
    return LlmRequestEvent(
        component="anthropic",
        service=int_service(None, integration.integration_config),
        resource="%s.%s" % (instance.__class__.__name__, func.__name__),
        integration_name="anthropic",
        provider="anthropic",
        model=kwargs.get("model", ""),
        integration=integration,
        submit_to_llmobs=True,
        request_kwargs=kwargs,
        base_tag_kwargs={
            "model": kwargs.get("model", ""),
            "interface_type": "chat_model",
            "provider": "anthropic",
            "instance": instance,
        },
    )


def traced_chat_model_generate(func, instance, args, kwargs):
    integration = anthropic._datadog_integration
    event = _create_llm_event(integration, instance, func, kwargs)

    with core.context_with_event(event) as ctx:
        chat_completions = None
        try:
            chat_completions = func(*args, **kwargs)
            if is_streaming_operation(chat_completions):
                ctx.set_item("is_stream", True)
                event._end_span = False
                return handle_streamed_response(integration, chat_completions, args, kwargs, ctx.span)
        finally:
            if not ctx.get_item("is_stream", False):
                ctx.set_item("response", chat_completions)
    return chat_completions


async def traced_async_chat_model_generate(func, instance, args, kwargs):
    integration = anthropic._datadog_integration
    event = _create_llm_event(integration, instance, func, kwargs)

    with core.context_with_event(event) as ctx:
        chat_completions = None
        try:
            chat_completions = await func(*args, **kwargs)
            if is_streaming_operation(chat_completions):
                ctx.set_item("is_stream", True)
                event._end_span = False
                return handle_streamed_response(integration, chat_completions, args, kwargs, ctx.span)
        finally:
            if not ctx.get_item("is_stream", False):
                ctx.set_item("response", chat_completions)
    return chat_completions


def patch():
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


def unpatch():
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

    delattr(anthropic, "_datadog_integration")
