COMPONENT = "openai"
# Operations whose requests are submitted to products such as LLM Observability.
SUBMITTED_OPERATION_IDS = (
    "createCompletion",
    "createChatCompletion",
    "createEmbedding",
    "createResponse",
    "parseChatCompletion",
    "parseResponse",
    "createDecision",
)
# Span context item holding the base URL of the client that made the request, so products can tell which
# provider (OpenAI, Azure OpenAI, DeepSeek...) served it.
BASE_URL = "openai.base_url"
# Default arguments of a server-side MCP tool call that reports none.
DEFAULT_TOOL_ARGUMENTS = "{}"


def provider_name(base_url: str) -> str:
    """The client name the openai.request.provider tag reports for requests sent to base_url."""
    base_url = base_url.lower()
    if "azure" in base_url:
        return "AzureOpenAI"
    if "deepseek" in base_url:
        return "Deepseek"
    return "OpenAI"
