from ddtrace.llmobs._contrib.llama_index.subscribers import LLMObsLlamaIndexSpanFinishingSubscriber
from ddtrace.llmobs._contrib.llama_index.subscribers import LLMObsLlamaIndexSpanStartedSubscriber
from ddtrace.llmobs._contrib.llama_index.subscribers import LLMObsLlamaIndexSpanStartingSubscriber


def listen() -> None:
    LLMObsLlamaIndexSpanStartingSubscriber.register()
    LLMObsLlamaIndexSpanStartedSubscriber.register()
    LLMObsLlamaIndexSpanFinishingSubscriber.register()


def unlisten() -> None:
    LLMObsLlamaIndexSpanStartingSubscriber.unregister()
    LLMObsLlamaIndexSpanStartedSubscriber.unregister()
    LLMObsLlamaIndexSpanFinishingSubscriber.unregister()
