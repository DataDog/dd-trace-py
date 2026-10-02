from ddtrace.llmobs._contrib.anthropic.subscribers import LLMObsAnthropicSpanFinishingSubscriber
from ddtrace.llmobs._contrib.anthropic.subscribers import LLMObsAnthropicSpanStartedSubscriber
from ddtrace.llmobs._contrib.anthropic.subscribers import LLMObsAnthropicSpanStartingSubscriber


def listen() -> None:
    LLMObsAnthropicSpanStartingSubscriber.register()
    LLMObsAnthropicSpanStartedSubscriber.register()
    LLMObsAnthropicSpanFinishingSubscriber.register()


def unlisten() -> None:
    LLMObsAnthropicSpanStartingSubscriber.unregister()
    LLMObsAnthropicSpanStartedSubscriber.unregister()
    LLMObsAnthropicSpanFinishingSubscriber.unregister()
