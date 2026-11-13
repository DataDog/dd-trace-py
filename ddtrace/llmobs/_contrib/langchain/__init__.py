from typing import Any
from typing import Optional

from ddtrace.contrib.internal.langchain.utils import STREAM_PULL_EVENT
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.llmobs._contrib._subscribers import get_integration
from ddtrace.llmobs._contrib._subscribers import register
from ddtrace.llmobs._contrib._subscribers import unregister
from ddtrace.llmobs._integrations.langchain import LangChainIntegration
from ddtrace.llmobs._llmobs import LLMObs


def _enabled_integration() -> Optional[LangChainIntegration]:
    integration = get_integration("langchain")
    if not isinstance(integration, LangChainIntegration) or not integration.llmobs_enabled:
        return None
    return integration


def _on_prompt_template_invoked(instance: Any, prompt: Any, args: Any, kwargs: dict[str, Any]) -> None:
    integration = _enabled_integration()
    if integration is not None:
        integration.handle_prompt_template_invoke(instance, prompt, args, kwargs)


def _on_language_model_invoke(instance: Any, args: Any, kwargs: dict[str, Any]) -> None:
    integration = _enabled_integration()
    if integration is not None:
        integration.handle_llm_invoke(instance, args, kwargs)


def _on_stream_pull(span: Any) -> None:
    # The tracer reactivates the stream's span before each pull; mirror it on the LLMObs context so
    # spans started while pulling the chunk keep the stream's span as their LLMObs parent.
    if span.span_type == SpanTypes.LLM and not span.finished and _enabled_integration() is not None:
        LLMObs._instance._llmobs_context_provider.activate(span)


def listen() -> None:
    register("langchain", LangChainIntegration)
    # Prompt templates are not spans, so they reach LLMObs through plain core events. The
    # listeners are deduplicated by identity and no-op once the component is unregistered.
    core.on("langchain.prompt_template.invoke.after", _on_prompt_template_invoked)
    core.on("langchain.language_model.invoke.before", _on_language_model_invoke)
    core.on(STREAM_PULL_EVENT, _on_stream_pull)


def unlisten() -> None:
    unregister("langchain")
