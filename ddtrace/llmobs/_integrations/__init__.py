"""LLMObs integration classes.

Integration patch modules import one concrete integration at a time. Resolve those
classes lazily so patching one integration does not import the full LLMObs service
through every other integration.
"""

from importlib import import_module
import sys
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.internal import core


_INTEGRATION_MODULES = {
    "AnthropicIntegration": ".anthropic",
    "BaseLLMIntegration": ".base",
    "BedrockIntegration": ".bedrock",
    "ClaudeAgentSdkIntegration": ".claude_agent_sdk",
    "CrewAIIntegration": ".crewai",
    "GoogleAdkIntegration": ".google_adk",
    "GoogleGenAIIntegration": ".google_genai",
    "LangChainIntegration": ".langchain",
    "LangGraphIntegration": ".langgraph",
    "LiteLLMIntegration": ".litellm",
    "LlamaIndexIntegration": ".llama_index",
    "MCPIntegration": ".mcp",
    "MistralAIIntegration": ".mistralai",
    "OpenAIAgentsIntegration": ".openai_agents",
    "OpenAIIntegration": ".openai",
    "PydanticAIIntegration": ".pydantic_ai",
    "VertexAIIntegration": ".vertexai",
    "VLLMIntegration": ".vllm",
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


# Each entry is (event prefix, third-party module to stash the integration on, concrete
# integration class name, extra constructor kwarg name for the module itself if the integration
# needs it). Registered here (rather than by contrib patch modules importing the concrete
# integration classes directly) so contrib -> ddtrace.llmobs stays a one-way, event-based
# notification: contrib dispatches "<prefix>.integration.create" and the listener below builds and
# stashes the integration object, instead of contrib importing and constructing it itself.
_INTEGRATION_LISTENER_SPECS = [
    ("anthropic", "anthropic", "AnthropicIntegration", None),
    ("llama_index", "llama_index.core", "LlamaIndexIntegration", None),
    ("bedrock", "botocore", "BedrockIntegration", None),
    ("claude_agent_sdk", "claude_agent_sdk", "ClaudeAgentSdkIntegration", None),
    ("crewai", "crewai", "CrewAIIntegration", None),
    ("google_adk", "google.adk", "GoogleAdkIntegration", None),
    ("google_genai", "google.genai", "GoogleGenAIIntegration", None),
    ("langchain", "langchain_core", "LangChainIntegration", None),
    ("langgraph", "langgraph", "LangGraphIntegration", None),
    ("litellm", "litellm", "LiteLLMIntegration", None),
    ("mcp", "mcp", "MCPIntegration", None),
    ("mistralai", "mistralai.client", "MistralAIIntegration", None),
    ("openai_agents", "agents", "OpenAIAgentsIntegration", None),
    ("openai", "openai", "OpenAIIntegration", "openai"),
    ("pydantic_ai", "pydantic_ai", "PydanticAIIntegration", None),
    ("vertexai", "vertexai", "VertexAIIntegration", None),
    ("vllm", "vllm", "VLLMIntegration", None),
]


def _make_integration_listener(module_path: str, class_name: str, module_kwarg: Optional[str]) -> Callable[[Any], None]:
    def _on_integration_create(integration_config: Any) -> None:
        # The third-party module is guaranteed to already be imported by the time this listener
        # runs: it's dispatched from within the corresponding ddtrace/contrib/internal/*/patch.py's
        # own patch() function, after that module's own import of the third-party package.
        module = import_module(module_path)

        # getattr() on this module (not a bare name reference) is required so it goes through
        # __getattr__ above and only imports the concrete integration module when this listener
        # runs.
        integration_cls = getattr(sys.modules[__name__], class_name)
        kwargs = {"integration_config": integration_config}
        if module_kwarg:
            kwargs[module_kwarg] = module
        module._datadog_integration = integration_cls(**kwargs)  # type: ignore[attr-defined]

    return _on_integration_create


for _event_prefix, _module_path, _class_name, _module_kwarg in _INTEGRATION_LISTENER_SPECS:
    core.on(f"{_event_prefix}.integration.create", _make_integration_listener(_module_path, _class_name, _module_kwarg))
