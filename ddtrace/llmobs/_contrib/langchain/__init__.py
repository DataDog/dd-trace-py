from typing import Any
from typing import Optional

from ddtrace.internal import core
from ddtrace.llmobs._contrib._subscribers import get_integration
from ddtrace.llmobs._contrib._subscribers import register
from ddtrace.llmobs._contrib._subscribers import unregister
from ddtrace.llmobs._integrations.langchain import LangChainIntegration


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


def listen() -> None:
    register("langchain", LangChainIntegration)
    # Prompt templates are not spans, so they reach LLMObs through plain core events. The
    # listeners are deduplicated by identity and no-op once the component is unregistered.
    core.on("langchain.prompt_template.invoke.after", _on_prompt_template_invoked)
    core.on("langchain.language_model.invoke.before", _on_language_model_invoke)


def unlisten() -> None:
    unregister("langchain")
