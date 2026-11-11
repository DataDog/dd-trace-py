from ddtrace.llmobs._contrib._subscribers import register
from ddtrace.llmobs._contrib._subscribers import unregister
from ddtrace.llmobs._integrations.google_genai import GoogleGenAIIntegration


def listen() -> None:
    register("google_genai", GoogleGenAIIntegration)


def unlisten() -> None:
    unregister("google_genai")
