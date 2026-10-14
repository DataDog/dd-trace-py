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
    # The finishing subscriber stays registered: requests and deferred streams that started
    # before unpatch() still need their output, token metrics, and shadow tags when they end.
    # It only acts on Anthropic contexts, and an unpatched client creates no new ones.
