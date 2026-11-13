from ddtrace.llmobs._contrib._subscribers import register
from ddtrace.llmobs._contrib._subscribers import unregister
from ddtrace.llmobs._integrations.vertexai import VertexAIIntegration


def listen() -> None:
    register("vertexai", VertexAIIntegration)


def unlisten() -> None:
    unregister("vertexai")
