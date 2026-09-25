"""LLMObs integration classes.

Integration patch modules import one concrete integration at a time. Resolve those
classes lazily so patching one integration does not import the full LLMObs service
through every other integration.
"""

from importlib import import_module
import sys
from typing import Any

from ddtrace.internal import core


_INTEGRATION_MODULES = {
    "AnthropicIntegration": ".anthropic",
    "BaseLLMIntegration": ".base",
    "BedrockIntegration": ".bedrock",
    "ClaudeAgentSdkIntegration": ".claude_agent_sdk",
    "GoogleAdkIntegration": ".google_adk",
    "GoogleGenAIIntegration": ".google_genai",
    "LangChainIntegration": ".langchain",
    "LiteLLMIntegration": ".litellm",
    "LlamaIndexIntegration": ".llama_index",
    "MistralAIIntegration": ".mistralai",
    "OpenAIIntegration": ".openai",
    "PydanticAIIntegration": ".pydantic_ai",
    "VertexAIIntegration": ".vertexai",
}


def __getattr__(name: str) -> Any:
    try:
        module_name = _INTEGRATION_MODULES[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None

    integration = getattr(import_module(module_name, __name__), name)
    globals()[name] = integration
    return integration


__all__ = list(_INTEGRATION_MODULES)


def _on_anthropic_integration_create(integration_config: Any) -> None:
    # anthropic (the third-party package) is guaranteed to already be imported by the time this
    # listener runs: it's dispatched from within ddtrace/contrib/internal/anthropic/patch.py's own
    # patch() function, after that module's own `import anthropic` at the top of the file.
    import anthropic

    # getattr() on this module (not a bare name reference) is required so it goes through
    # __getattr__ above and only imports the concrete integration module when this listener runs.
    anthropic._datadog_integration = getattr(sys.modules[__name__], "AnthropicIntegration")(
        integration_config=integration_config
    )


# Registered here (rather than by contrib patch modules importing the concrete integration classes
# directly) so contrib -> ddtrace.llmobs stays a one-way, event-based notification: contrib
# dispatches "anthropic.integration.create" and this listener builds and stashes the integration
# object, instead of contrib importing and constructing AnthropicIntegration itself.
core.on("anthropic.integration.create", _on_anthropic_integration_create)
