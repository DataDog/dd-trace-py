COMPONENT = "pydantic_ai"
TAG_MODEL = "pydantic_ai.request.model"
TAG_PROVIDER = "pydantic_ai.request.provider"
AGENT_SPAN_NAME = "pydantic_ai.agent"
TOOL_SPAN_NAME = "pydantic_ai.tool"

# in some cases, PydanticAI uses a different provider name than what we expect
PYDANTIC_AI_SYSTEM_TO_PROVIDER = {
    "google-gla": "google",
    "google-vertex": "google",
}
