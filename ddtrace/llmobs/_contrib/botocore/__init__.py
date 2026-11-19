from ddtrace.contrib.internal.botocore.constants import BEDROCK_COMPONENT
from ddtrace.llmobs._contrib._subscribers import register
from ddtrace.llmobs._contrib._subscribers import unregister
from ddtrace.llmobs._integrations import BedrockIntegration


def listen() -> None:
    register(BEDROCK_COMPONENT, BedrockIntegration)


def unlisten() -> None:
    unregister(BEDROCK_COMPONENT)
