"""Lazy subscriber bundles owned by tracing."""

from dataclasses import dataclass
from dataclasses import field
import sys
from types import ModuleType

from ddtrace.internal.core import event_hub
from ddtrace.internal.core.subscriber import SubscriberBundle
from ddtrace.internal.module import ModuleWatchdog


@dataclass(frozen=True)
class _WebFrameworkSubscriberBundle(SubscriberBundle):
    """Tracing-owned admission policy shared by product startup and integration setup."""

    _enabled: bool = field(default=True, init=False, compare=False, repr=False)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def ensure_registered(self) -> None:
        # Standalone span requirements are independent of APM listener admission.
        STANDALONE_WEB_FRAMEWORK_SUBSCRIBERS.ensure_registered()
        if self._enabled:
            super().ensure_registered()

    def register(self) -> None:
        """Allow APM requests again; ordinary setup only ensures the current policy."""
        with event_hub._registration_lock:
            object.__setattr__(self, "_enabled", True)
            object.__setattr__(self, "_registered_generation", -1)
        self.ensure_registered()

    def unregister(self) -> None:
        """Close admission immediately and drain completion listeners asynchronously."""
        with event_hub._registration_lock:
            object.__setattr__(self, "_enabled", False)
            object.__setattr__(self, "_registered_generation", -1)
            resolved = self._resolved
            if resolved is None:
                module = sys.modules.get(self.module)
                # An overlapping import can publish its module before defining classes.
                # Its eventual register() call also checks this bundle's admission policy.
                if module is None or any(not hasattr(module, name) for name in self.subscribers):
                    return
                resolved = self._resolve(module)
            for subscriber in resolved:
                subscriber.unregister()


SPAN_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers._base",
    subscribers=("TracingSubscriber",),
)
HTTP_CLIENT_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.http_client",
    subscribers=("HttpClientTracingSubscriber",),
)
WEB_FRAMEWORK_SUBSCRIBERS = _WebFrameworkSubscriberBundle(
    module="ddtrace._trace.subscribers.web_framework",
    subscribers=("WebFrameworkRequestSubscriber",),
)
STANDALONE_WEB_FRAMEWORK_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.web_framework",
    subscribers=("StandaloneWebFrameworkRequestSubscriber",),
)
MESSAGING_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.messaging",
    subscribers=("MessagingProduceSubscriber", "MessagingConsumeSubscriber"),
)
KAFKA_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.kafka",
    subscribers=("KafkaProduceSubscriber", "KafkaConsumeSubscriber"),
)
LLM_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.llm",
    subscribers=("LlmTracingSubscriber",),
)
MOLTEN_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.molten",
    subscribers=("MoltenRouteSubscriber",),
)
AWS_DURABLE_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.aws_durable",
    subscribers=("AwsDurableExecuteSubscriber", "AwsDurableInvokeSubscriber", "AwsDurableOperationSubscriber"),
)
RAY_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.ray",
    subscribers=(
        "RayJobStartSubscriber",
        "RayExecutionSubscriber",
        "RayCoreAPITracingSubscriber",
        "RaySubmissionSubscriber",
        "RayContextInjectionSubscriber",
    ),
)

# All SDK-independent tracing subscribers are activated by tracing startup.
SUBSCRIBER_BUNDLES = (
    WEB_FRAMEWORK_SUBSCRIBERS,
    SPAN_SUBSCRIBERS,
    HTTP_CLIENT_SUBSCRIBERS,
    MESSAGING_SUBSCRIBERS,
    KAFKA_SUBSCRIBERS,
    LLM_SUBSCRIBERS,
    MOLTEN_SUBSCRIBERS,
    AWS_DURABLE_SUBSCRIBERS,
)


_ray_hook_registered = False


def _register_ray_subscribers(module: ModuleType) -> None:
    RAY_SUBSCRIBERS.ensure_registered()


def initialize_subscribers(bundles: tuple[SubscriberBundle, ...] = SUBSCRIBER_BUNDLES) -> None:
    """Activate tracing-owned listeners without coupling integrations to their consumers."""
    global _ray_hook_registered

    for bundle in bundles:
        bundle.ensure_registered()

    # Ray's subscriber helpers require its optional SDK. Tracing owns this hook
    # so the integration never needs to import or activate tracing subscribers.
    if not _ray_hook_registered:
        ModuleWatchdog.register_module_hook("ddtrace.contrib.internal.ray.patch", _register_ray_subscribers)
        _ray_hook_registered = True
    elif "ddtrace.contrib.internal.ray.patch" in sys.modules:
        RAY_SUBSCRIBERS.ensure_registered()
