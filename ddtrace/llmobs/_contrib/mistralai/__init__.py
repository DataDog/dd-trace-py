from ddtrace.llmobs._contrib._subscribers import register
from ddtrace.llmobs._contrib._subscribers import unregister
from ddtrace.llmobs._integrations.mistralai import MistralAIIntegration


def listen() -> None:
    register("mistralai", MistralAIIntegration)


def unlisten() -> None:
    unregister("mistralai")
