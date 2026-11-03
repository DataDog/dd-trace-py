"""Lazy subscriber bundles owned by LLMObs."""

from ddtrace.internal.products import SubscriberBundle


LLM_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace.llmobs._subscribers",
    subscribers=("LLMObsLlmSubscriber",),
)
