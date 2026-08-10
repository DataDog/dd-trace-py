"""LLMObs integration classes.

Integration patch modules import one concrete integration at a time. Resolve those
classes lazily so patching one integration does not import the full LLMObs service
through every other integration.
"""

from importlib import import_module
from typing import Any


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
