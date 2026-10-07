from ddtrace.llmobs._contrib.mistralai.subscribers import LLMObsMistralAISpanFinishingSubscriber
from ddtrace.llmobs._contrib.mistralai.subscribers import LLMObsMistralAISpanStartedSubscriber
from ddtrace.llmobs._contrib.mistralai.subscribers import LLMObsMistralAISpanStartingSubscriber


def listen() -> None:
    LLMObsMistralAISpanStartingSubscriber.register()
    LLMObsMistralAISpanStartedSubscriber.register()
    LLMObsMistralAISpanFinishingSubscriber.register()


def unlisten() -> None:
    LLMObsMistralAISpanStartingSubscriber.unregister()
    LLMObsMistralAISpanStartedSubscriber.unregister()
    LLMObsMistralAISpanFinishingSubscriber.unregister()
    LLMObsMistralAISpanStartingSubscriber.forget_integration()
