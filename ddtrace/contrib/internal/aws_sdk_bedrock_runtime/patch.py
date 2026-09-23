from importlib.metadata import version
from typing import Any
from typing import Callable

import aws_sdk_bedrock_runtime
from aws_sdk_bedrock_runtime.client import AsyncBedrockRuntimeClient

from ddtrace import config
from ddtrace.contrib._events.aws_sdk_bedrock_runtime import BedrockBidirectionalStreamEvent
from ddtrace.contrib.internal.aws_sdk_bedrock_runtime._stream import DuplexProxy
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.trace import Context
from ddtrace.trace import tracer


log = get_logger(__name__)
config._add("aws_sdk_bedrock_runtime", {})  # type: ignore[no-untyped-call]  # Shared config registration is untyped.


def get_version() -> str:
    return version("aws-sdk-bedrock-runtime")


def _supported_versions() -> dict[str, str]:
    return {"aws_sdk_bedrock_runtime": ">=0.11.0"}


async def traced_invoke(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    request = args[0] if args else kwargs.get("input")
    model = getattr(request, "model_id", "")
    if not core.has_listeners(BedrockBidirectionalStreamEvent.event_name):
        return await func(*args, **kwargs)
    try:
        event = BedrockBidirectionalStreamEvent(
            integration_config=config.aws_sdk_bedrock_runtime,
            model=model,
            parent=tracer.context_provider.active() or Context(),
        )
        core.dispatch_event(event)
        state = event.observer
    except Exception:
        log.debug("Cannot initialize Nova Sonic tracing", exc_info=True)
        return await func(*args, **kwargs)
    if state is None:
        return await func(*args, **kwargs)
    try:
        stream = await func(*args, **kwargs)
    except BaseException:
        state.finish_error()
        raise
    try:
        return DuplexProxy(stream, state)
    except Exception:
        log.debug("Cannot wrap Nova Sonic stream", exc_info=True)
        state.finish()
        return stream


def patch() -> None:
    if getattr(aws_sdk_bedrock_runtime, "_datadog_patch", False):
        return
    wrap(AsyncBedrockRuntimeClient, "invoke_model_with_bidirectional_stream", traced_invoke)
    aws_sdk_bedrock_runtime._datadog_patch = True


def unpatch() -> None:
    if not getattr(aws_sdk_bedrock_runtime, "_datadog_patch", False):
        return
    unwrap(AsyncBedrockRuntimeClient, "invoke_model_with_bidirectional_stream")
    aws_sdk_bedrock_runtime._datadog_patch = False
