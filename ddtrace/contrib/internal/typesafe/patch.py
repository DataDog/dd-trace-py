import sys
from typing import Any
from typing import Callable

import typesafe_sdk

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.llmobs._integrations import TypeSafeIntegration


config._add("typesafe", {})  # type: ignore[no-untyped-call]


def get_version() -> str:
    return getattr(typesafe_sdk, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"typesafe_sdk": ">=0.6.0"}


def _request_kwargs(instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    # state and questions are the only positional parameters of system_one
    request_kwargs = dict(zip(("state", "questions"), args))
    request_kwargs.update(kwargs)
    if request_kwargs.get("model") is None:
        request_kwargs["model"] = getattr(getattr(instance, "_config", None), "default_model", None)
    return request_kwargs


def _request_event(integration: TypeSafeIntegration, instance: Any, request_kwargs: dict[str, Any]) -> LlmRequestEvent:
    return LlmRequestEvent(
        component="typesafe",
        integration_config=config.typesafe,
        service=int_service(None, integration.integration_config),
        resource=f"{instance.__class__.__name__}.system_one",
        provider="typesafe",
        model=request_kwargs.get("model") or "",
        llmobs_integration=integration,
        submit_to_llmobs=True,
        request_kwargs=request_kwargs,
        instance=instance,
    )


def traced_system_one(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    integration: TypeSafeIntegration = typesafe_sdk._datadog_integration
    event = _request_event(integration, instance, _request_kwargs(instance, args, kwargs))
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            resp = func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        event.response = resp
        ctx.dispatch_ended_event()
        return resp


async def traced_async_system_one(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    integration: TypeSafeIntegration = typesafe_sdk._datadog_integration
    event = _request_event(integration, instance, _request_kwargs(instance, args, kwargs))
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            resp = await func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        event.response = resp
        ctx.dispatch_ended_event()
        return resp


def patch() -> None:
    if getattr(typesafe_sdk, "_datadog_patch", False):
        return

    typesafe_sdk._datadog_patch = True
    typesafe_sdk._datadog_integration = TypeSafeIntegration(integration_config=config.typesafe)

    wrap("typesafe_sdk", "TypeSafeClient.system_one", traced_system_one)
    wrap("typesafe_sdk", "AsyncTypeSafeClient.system_one", traced_async_system_one)


def unpatch() -> None:
    if not getattr(typesafe_sdk, "_datadog_patch", False):
        return

    typesafe_sdk._datadog_patch = False

    unwrap(typesafe_sdk.TypeSafeClient, "system_one")
    unwrap(typesafe_sdk.AsyncTypeSafeClient, "system_one")

    delattr(typesafe_sdk, "_datadog_integration")
