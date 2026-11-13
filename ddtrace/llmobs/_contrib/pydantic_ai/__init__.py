from ddtrace.llmobs._contrib._subscribers import register
from ddtrace.llmobs._contrib._subscribers import unregister
from ddtrace.llmobs._integrations.pydantic_ai import PydanticAIIntegration


def listen() -> None:
    register("pydantic_ai", PydanticAIIntegration)


def unlisten() -> None:
    unregister("pydantic_ai")
