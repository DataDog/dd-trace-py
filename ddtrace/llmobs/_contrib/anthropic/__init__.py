from ddtrace.llmobs._contrib._subscribers import register
from ddtrace.llmobs._contrib._subscribers import unregister
from ddtrace.llmobs._integrations.anthropic import AnthropicIntegration


def listen() -> None:
    register("anthropic", AnthropicIntegration)


def unlisten() -> None:
    unregister("anthropic")
