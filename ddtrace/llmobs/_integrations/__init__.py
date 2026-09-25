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


def _on_llama_index_integration_create(integration_config: Any) -> None:
    # llama_index.core is guaranteed to already be imported by the time this listener runs: it's
    # dispatched from within ddtrace/contrib/internal/llama_index/patch.py's own patch() function,
    # after that module's own `import llama_index.core as llama_core` at the top of the file.
    import llama_index.core as llama_core

    # getattr() on this module (not a bare name reference) is required so it goes through
    # __getattr__ above and only imports the concrete integration module when this listener runs.
    llama_core._datadog_integration = getattr(sys.modules[__name__], "LlamaIndexIntegration")(
        integration_config=integration_config
    )


# Registered here (rather than by contrib patch modules importing the concrete integration classes
# directly) so contrib -> ddtrace.llmobs stays a one-way, event-based notification: contrib
# dispatches "llama_index.integration.create" and this listener builds and stashes the integration
# object, instead of contrib importing and constructing LlamaIndexIntegration itself.
core.on("llama_index.integration.create", _on_llama_index_integration_create)


def _on_bedrock_integration_create(integration_config: Any) -> None:
    # botocore is guaranteed to already be imported by the time this listener runs: it's
    # dispatched from within ddtrace/contrib/internal/botocore/patch.py's own patch() function,
    # after that module's own `import botocore.client` at the top of the file.
    import botocore

    # getattr() on this module (not a bare name reference) is required so it goes through
    # __getattr__ above and only imports the concrete integration module when this listener runs.
    botocore._datadog_integration = getattr(sys.modules[__name__], "BedrockIntegration")(
        integration_config=integration_config
    )


# Registered here (rather than by contrib patch modules importing the concrete integration classes
# directly) so contrib -> ddtrace.llmobs stays a one-way, event-based notification: contrib
# dispatches "bedrock.integration.create" and this listener builds and stashes the integration
# object, instead of contrib importing and constructing BedrockIntegration itself.
core.on("bedrock.integration.create", _on_bedrock_integration_create)


def _on_claude_agent_sdk_integration_create(integration_config: Any) -> None:
    # claude_agent_sdk is guaranteed to already be imported by the time this listener runs: it's
    # dispatched from within ddtrace/contrib/internal/claude_agent_sdk/patch.py's own patch()
    # function, after that module's own `import claude_agent_sdk` at the top of the file.
    import claude_agent_sdk

    # getattr() on this module (not a bare name reference) is required so it goes through
    # __getattr__ above and only imports the concrete integration module when this listener runs.
    claude_agent_sdk._datadog_integration = getattr(sys.modules[__name__], "ClaudeAgentSdkIntegration")(
        integration_config=integration_config
    )


# Registered here (rather than by contrib patch modules importing the concrete integration classes
# directly) so contrib -> ddtrace.llmobs stays a one-way, event-based notification: contrib
# dispatches "claude_agent_sdk.integration.create" and this listener builds and stashes the
# integration object, instead of contrib importing and constructing ClaudeAgentSdkIntegration itself.
core.on("claude_agent_sdk.integration.create", _on_claude_agent_sdk_integration_create)


def _on_crewai_integration_create(integration_config: Any) -> None:
    # crewai is guaranteed to already be imported by the time this listener runs: it's dispatched
    # from within ddtrace/contrib/internal/crewai/patch.py's own patch() function, after that
    # module's own `import crewai` at the top of the file.
    import crewai

    # getattr() on this module (not a bare name reference) is required so it goes through
    # __getattr__ above and only imports the concrete integration module when this listener runs.
    crewai._datadog_integration = getattr(sys.modules[__name__], "CrewAIIntegration")(
        integration_config=integration_config
    )


# Registered here (rather than by contrib patch modules importing the concrete integration classes
# directly) so contrib -> ddtrace.llmobs stays a one-way, event-based notification: contrib
# dispatches "crewai.integration.create" and this listener builds and stashes the integration
# object, instead of contrib importing and constructing CrewAIIntegration itself.
core.on("crewai.integration.create", _on_crewai_integration_create)


def _on_google_adk_integration_create(integration_config: Any) -> None:
    # google.adk is guaranteed to already be imported by the time this listener runs: it's
    # dispatched from within ddtrace/contrib/internal/google_adk/patch.py's own patch() function,
    # after that module's own `import google.adk as adk` at the top of the file.
    import google.adk as adk

    # getattr() on this module (not a bare name reference) is required so it goes through
    # __getattr__ above and only imports the concrete integration module when this listener runs.
    adk._datadog_integration = getattr(sys.modules[__name__], "GoogleAdkIntegration")(
        integration_config=integration_config
    )


# Registered here (rather than by contrib patch modules importing the concrete integration classes
# directly) so contrib -> ddtrace.llmobs stays a one-way, event-based notification: contrib
# dispatches "google_adk.integration.create" and this listener builds and stashes the integration
# object, instead of contrib importing and constructing GoogleAdkIntegration itself.
core.on("google_adk.integration.create", _on_google_adk_integration_create)


def _on_google_genai_integration_create(integration_config: Any) -> None:
    # google.genai is guaranteed to already be imported by the time this listener runs: it's
    # dispatched from within ddtrace/contrib/internal/google_genai/patch.py's own patch() function,
    # after that module's own `from google import genai` at the top of the file.
    from google import genai  # type: ignore[attr-defined]

    # getattr() on this module (not a bare name reference) is required so it goes through
    # __getattr__ above and only imports the concrete integration module when this listener runs.
    genai._datadog_integration = getattr(sys.modules[__name__], "GoogleGenAIIntegration")(
        integration_config=integration_config
    )


# Registered here (rather than by contrib patch modules importing the concrete integration classes
# directly) so contrib -> ddtrace.llmobs stays a one-way, event-based notification: contrib
# dispatches "google_genai.integration.create" and this listener builds and stashes the integration
# object, instead of contrib importing and constructing GoogleGenAIIntegration itself.
core.on("google_genai.integration.create", _on_google_genai_integration_create)


def _on_langchain_integration_create(integration_config: Any) -> None:
    # langchain_core is guaranteed to already be imported by the time this listener runs: it's
    # dispatched from within ddtrace/contrib/internal/langchain/patch.py's own patch() function,
    # after that module's own `import langchain_core` at the top of the file.
    import langchain_core

    # getattr() on this module (not a bare name reference) is required so it goes through
    # __getattr__ above and only imports the concrete integration module when this listener runs.
    langchain_core._datadog_integration = getattr(sys.modules[__name__], "LangChainIntegration")(
        integration_config=integration_config
    )


# Registered here (rather than by contrib patch modules importing the concrete integration classes
# directly) so contrib -> ddtrace.llmobs stays a one-way, event-based notification: contrib
# dispatches "langchain.integration.create" and this listener builds and stashes the integration
# object, instead of contrib importing and constructing LangChainIntegration itself.
core.on("langchain.integration.create", _on_langchain_integration_create)


def _on_langgraph_integration_create(integration_config: Any) -> None:
    # langgraph is guaranteed to already be imported by the time this listener runs: it's
    # dispatched from within ddtrace/contrib/internal/langgraph/patch.py's own patch() function,
    # after that module's own `import langgraph` at the top of the file.
    import langgraph

    # getattr() on this module (not a bare name reference) is required so it goes through
    # __getattr__ above and only imports the concrete integration module when this listener runs.
    langgraph._datadog_integration = getattr(sys.modules[__name__], "LangGraphIntegration")(
        integration_config=integration_config
    )


# Registered here (rather than by contrib patch modules importing the concrete integration classes
# directly) so contrib -> ddtrace.llmobs stays a one-way, event-based notification: contrib
# dispatches "langgraph.integration.create" and this listener builds and stashes the integration
# object, instead of contrib importing and constructing LangGraphIntegration itself.
core.on("langgraph.integration.create", _on_langgraph_integration_create)


def _on_litellm_integration_create(integration_config: Any) -> None:
    # litellm is guaranteed to already be imported by the time this listener runs: it's dispatched
    # from within ddtrace/contrib/internal/litellm/patch.py's own patch() function, after that
    # module's own `import litellm` at the top of the file.
    import litellm

    # getattr() on this module (not a bare name reference) is required so it goes through
    # __getattr__ above and only imports the concrete integration module when this listener runs.
    litellm._datadog_integration = getattr(sys.modules[__name__], "LiteLLMIntegration")(
        integration_config=integration_config
    )


# Registered here (rather than by contrib patch modules importing the concrete integration classes
# directly) so contrib -> ddtrace.llmobs stays a one-way, event-based notification: contrib
# dispatches "litellm.integration.create" and this listener builds and stashes the integration
# object, instead of contrib importing and constructing LiteLLMIntegration itself.
core.on("litellm.integration.create", _on_litellm_integration_create)


def _on_mcp_integration_create(integration_config: Any) -> None:
    # mcp is guaranteed to already be imported by the time this listener runs: it's dispatched
    # from within ddtrace/contrib/internal/mcp/patch.py's own patch() function, after that module's
    # own `import mcp` at the top of the file.
    import mcp

    # getattr() on this module (not a bare name reference) is required so it goes through
    # __getattr__ above and only imports the concrete integration module when this listener runs.
    mcp._datadog_integration = getattr(sys.modules[__name__], "MCPIntegration")(integration_config=integration_config)


# Registered here (rather than by contrib patch modules importing the concrete integration classes
# directly) so contrib -> ddtrace.llmobs stays a one-way, event-based notification: contrib
# dispatches "mcp.integration.create" and this listener builds and stashes the integration
# object, instead of contrib importing and constructing MCPIntegration itself.
core.on("mcp.integration.create", _on_mcp_integration_create)


def _on_mistralai_integration_create(integration_config: Any) -> None:
    # mistralai.client is guaranteed to already be imported by the time this listener runs: it's
    # dispatched from within ddtrace/contrib/internal/mistralai/patch.py's own patch() function,
    # after that module's own `from mistralai import client` at the top of the file.
    from mistralai import client

    # getattr() on this module (not a bare name reference) is required so it goes through
    # __getattr__ above and only imports the concrete integration module when this listener runs.
    client._datadog_integration = getattr(sys.modules[__name__], "MistralAIIntegration")(
        integration_config=integration_config
    )


# Registered here (rather than by contrib patch modules importing the concrete integration classes
# directly) so contrib -> ddtrace.llmobs stays a one-way, event-based notification: contrib
# dispatches "mistralai.integration.create" and this listener builds and stashes the integration
# object, instead of contrib importing and constructing MistralAIIntegration itself.
core.on("mistralai.integration.create", _on_mistralai_integration_create)


def _on_openai_agents_integration_create(integration_config: Any) -> None:
    # agents is guaranteed to already be imported by the time this listener runs: it's dispatched
    # from within ddtrace/contrib/internal/openai_agents/patch.py's own patch() function, after
    # that module's own `import agents` at the top of the file.
    import agents

    # getattr() on this module (not a bare name reference) is required so it goes through
    # __getattr__ above and only imports the concrete integration module when this listener runs.
    agents._datadog_integration = getattr(sys.modules[__name__], "OpenAIAgentsIntegration")(
        integration_config=integration_config
    )


# Registered here (rather than by contrib patch modules importing the concrete integration classes
# directly) so contrib -> ddtrace.llmobs stays a one-way, event-based notification: contrib
# dispatches "openai_agents.integration.create" and this listener builds and stashes the
# integration object, instead of contrib importing and constructing OpenAIAgentsIntegration itself.
core.on("openai_agents.integration.create", _on_openai_agents_integration_create)


def _on_openai_integration_create(integration_config: Any) -> None:
    # openai is guaranteed to already be imported by the time this listener runs: it's dispatched
    # from within ddtrace/contrib/internal/openai/patch.py's own patch() function, after that
    # module's own `import openai` at the top of the file.
    import openai

    # getattr() on this module (not a bare name reference) is required so it goes through
    # __getattr__ above and only imports the concrete integration module when this listener runs.
    openai._datadog_integration = getattr(sys.modules[__name__], "OpenAIIntegration")(
        integration_config=integration_config, openai=openai
    )


# Registered here (rather than by contrib patch modules importing the concrete integration classes
# directly) so contrib -> ddtrace.llmobs stays a one-way, event-based notification: contrib
# dispatches "openai.integration.create" and this listener builds and stashes the integration
# object, instead of contrib importing and constructing OpenAIIntegration itself.
core.on("openai.integration.create", _on_openai_integration_create)


def _on_pydantic_ai_integration_create(integration_config: Any) -> None:
    # pydantic_ai is guaranteed to already be imported by the time this listener runs: it's
    # dispatched from within ddtrace/contrib/internal/pydantic_ai/patch.py's own patch() function,
    # after that module's own `import pydantic_ai` at the top of the file.
    import pydantic_ai

    # getattr() on this module (not a bare name reference) is required so it goes through
    # __getattr__ above and only imports the concrete integration module when this listener runs.
    pydantic_ai._datadog_integration = getattr(sys.modules[__name__], "PydanticAIIntegration")(
        integration_config=integration_config
    )


# Registered here (rather than by contrib patch modules importing the concrete integration classes
# directly) so contrib -> ddtrace.llmobs stays a one-way, event-based notification: contrib
# dispatches "pydantic_ai.integration.create" and this listener builds and stashes the integration
# object, instead of contrib importing and constructing PydanticAIIntegration itself.
core.on("pydantic_ai.integration.create", _on_pydantic_ai_integration_create)


def _on_vertexai_integration_create(integration_config: Any) -> None:
    # vertexai is guaranteed to already be imported by the time this listener runs: it's dispatched
    # from within ddtrace/contrib/internal/vertexai/patch.py's own patch() function, after that
    # module's own `import vertexai` at the top of the file.
    import vertexai

    # getattr() on this module (not a bare name reference) is required so it goes through
    # __getattr__ above and only imports the concrete integration module when this listener runs.
    vertexai._datadog_integration = getattr(sys.modules[__name__], "VertexAIIntegration")(
        integration_config=integration_config
    )


# Registered here (rather than by contrib patch modules importing the concrete integration classes
# directly) so contrib -> ddtrace.llmobs stays a one-way, event-based notification: contrib
# dispatches "vertexai.integration.create" and this listener builds and stashes the integration
# object, instead of contrib importing and constructing VertexAIIntegration itself.
core.on("vertexai.integration.create", _on_vertexai_integration_create)


def _on_vllm_integration_create(integration_config: Any) -> None:
    # vllm is guaranteed to already be imported by the time this listener runs: it's dispatched
    # from within ddtrace/contrib/internal/vllm/patch.py's own patch() function, after that
    # module's own `import vllm` at the top of the file.
    import vllm

    # getattr() on this module (not a bare name reference) is required so it goes through
    # __getattr__ above and only imports the concrete integration module when this listener runs.
    vllm._datadog_integration = getattr(sys.modules[__name__], "VLLMIntegration")(integration_config=integration_config)


# Registered here (rather than by contrib patch modules importing the concrete integration classes
# directly) so contrib -> ddtrace.llmobs stays a one-way, event-based notification: contrib
# dispatches "vllm.integration.create" and this listener builds and stashes the integration
# object, instead of contrib importing and constructing VLLMIntegration itself.
core.on("vllm.integration.create", _on_vllm_integration_create)
