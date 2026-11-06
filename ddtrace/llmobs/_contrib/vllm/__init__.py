from ddtrace.llmobs._contrib._subscribers import register
from ddtrace.llmobs._contrib._subscribers import unregister
from ddtrace.llmobs._integrations.vllm import VLLMIntegration


def listen() -> None:
    register("vllm", VLLMIntegration)


def unlisten() -> None:
    unregister("vllm")
