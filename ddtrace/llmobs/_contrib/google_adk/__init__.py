from ddtrace.contrib.internal.google_adk._constants import COMPONENT
from ddtrace.llmobs._contrib._subscribers import register
from ddtrace.llmobs._contrib._subscribers import unregister
from ddtrace.llmobs._integrations.google_adk import GoogleAdkIntegration


def listen() -> None:
    register(COMPONENT, GoogleAdkIntegration)


def unlisten() -> None:
    unregister(COMPONENT)
