"""Lazy subscriber bundles owned by LLMObs."""

from ddtrace.internal.core.subscriber import SubscriberBundle


LLM_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace.llmobs._subscribers",
    subscribers=("LLMObsLlmSubscriber",),
)
