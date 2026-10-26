from ddtrace.llmobs._contrib._subscribers import register
from ddtrace.llmobs._contrib._subscribers import unregister
from ddtrace.llmobs._integrations.llama_index import LlamaIndexIntegration


def listen() -> None:
    register("llama_index", LlamaIndexIntegration)


def unlisten() -> None:
    unregister("llama_index")
