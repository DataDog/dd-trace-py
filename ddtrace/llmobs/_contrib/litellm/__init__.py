from ddtrace.llmobs._contrib._subscribers import register
from ddtrace.llmobs._contrib._subscribers import unregister
from ddtrace.llmobs._integrations.litellm import LiteLLMIntegration


def listen() -> None:
    register("litellm", LiteLLMIntegration)


def unlisten() -> None:
    unregister("litellm")
