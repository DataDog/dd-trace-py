from typing import Any
from typing import Callable

import typesafe_sdk

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.contrib.internal.typesafe._constants import ATTACHED_REFERENCES
from ddtrace.contrib.internal.typesafe._constants import COMPONENT
from ddtrace.contrib.internal.typesafe._constants import TAG_MODEL
from ddtrace.internal import core
from ddtrace.internal import integration_hooks
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

config._add("typesafe", {})  # type: ignore[no-untyped-call]


def get_version() -> str:
    return getattr(typesafe_sdk, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"typesafe_sdk": ">=0.6.0"}


def _request_kwargs(instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    # state and questions are the only positional parameters of system_one
    request_kwargs = dict(zip(("state", "questions"), args))
    request_kwargs.update(kwargs)
    # extra_body is shallow-merged over the request body, so a model in it is the one sent
    extra_body = request_kwargs.get("extra_body") or {}
    if "model" in extra_body:
        request_kwargs["model"] = extra_body["model"]
    elif request_kwargs.get("model") is None:
        request_kwargs["model"] = getattr(getattr(instance, "_config", None), "default_model", None)
    # Images attached with client.attach_assets() (typesafe-sdk 0.7.5+) are referenced from text, so the SDK only
    # knows to upload them through this snapshot; read it now, while the attach block is still active
    try:
        request_kwargs[ATTACHED_REFERENCES] = list(instance._attached.snapshot())
    except Exception:
        log.debug("Could not read the client's attached typesafe images", exc_info=True)
    return request_kwargs


def _request_event(instance: Any, request_kwargs: dict[str, Any]) -> LlmRequestEvent:
    model = request_kwargs.get("model") or ""
    return LlmRequestEvent(
        component=COMPONENT,
        integration_config=config.typesafe,
        service=int_service(None, config.typesafe),
        resource=f"{instance.__class__.__name__}.system_one",
        provider="typesafe",
        model=model,
        # Leave the tag off when no model is known rather than tagging an empty value.
        tags={TAG_MODEL: model} if model else {},
        submit_to_llmobs=True,
        request_kwargs=request_kwargs,
        instance=instance,
    )


def traced_system_one(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    event = _request_event(instance, _request_kwargs(instance, args, kwargs))
    with core.context_with_event(event):
        event.response = func(*args, **kwargs)
        return event.response


async def traced_async_system_one(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    event = _request_event(instance, _request_kwargs(instance, args, kwargs))
    with core.context_with_event(event):
        event.response = await func(*args, **kwargs)
        return event.response


def patch() -> None:
    if getattr(typesafe_sdk, "_datadog_patch", False):
        return

    typesafe_sdk._datadog_patch = True

    wrap("typesafe_sdk", "TypeSafeClient.system_one", traced_system_one)
    wrap("typesafe_sdk", "AsyncTypeSafeClient.system_one", traced_async_system_one)

    # Let products (LLMObs) attach their own LlmEvents subscribers without this
    # module importing them.
    integration_hooks.on_patch("typesafe")


def unpatch() -> None:
    if not getattr(typesafe_sdk, "_datadog_patch", False):
        return

    typesafe_sdk._datadog_patch = False

    integration_hooks.on_unpatch("typesafe")

    unwrap(typesafe_sdk.TypeSafeClient, "system_one")
    unwrap(typesafe_sdk.AsyncTypeSafeClient, "system_one")
