from ddtrace.llmobs._contrib._subscribers import register
from ddtrace.llmobs._contrib._subscribers import unregister
from ddtrace.llmobs._integrations.typesafe import TypeSafeIntegration


def listen() -> None:
    register("typesafe", TypeSafeIntegration)


def unlisten() -> None:
    unregister("typesafe")
