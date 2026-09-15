from collections.abc import Callable
from collections.abc import Mapping
from typing import Any
from typing import Optional
from typing import cast

import temporalio
import temporalio.activity
import temporalio.api.common.v1
import temporalio.client
import temporalio.converter
import temporalio.worker

from ddtrace import config
from ddtrace._trace.context import Context
from ddtrace._trace.events import TracingEvent
import ddtrace._trace.subscribers.temporalio  # noqa: F401
from ddtrace.contrib._events.temporalio import TemporalQueryWorkflowEvent
from ddtrace.contrib._events.temporalio import TemporalRunActivityEvent
from ddtrace.contrib._events.temporalio import TemporalSignalWorkflowEvent
from ddtrace.contrib._events.temporalio import TemporalStartWorkflowEvent
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.span_bus import span_from_context
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import Span


config._add("temporalio", {})  # type: ignore[no-untyped-call]


log = get_logger(__name__)

_CONTEXT_HEADER = "_datadog"
_BASE_TAGS = {"messaging.system": "temporal"}


def get_version() -> str:
    return str(getattr(temporalio, "__version__", ""))


def _supported_versions() -> dict[str, str]:
    return {"temporalio": ">=1.0.0"}


def _service() -> Optional[str]:
    return cast(Optional[str], config.temporalio.get("service"))


def _enabled() -> bool:
    return config.temporalio.get("enabled") is not False


def _inject_context(input_data: Any, span: Span) -> None:
    carrier: dict[str, str] = {}
    try:
        HTTPPropagator.inject(span, carrier)
        if carrier:
            payload = temporalio.converter.PayloadConverter.default.to_payloads([carrier])[0]
            input_data.headers = {**input_data.headers, _CONTEXT_HEADER: payload}
    except Exception:
        log.debug("Failed to inject trace context into Temporal headers", exc_info=True)


def _extract_context(headers: Mapping[str, temporalio.api.common.v1.Payload]) -> Optional[Context]:
    payload = headers.get(_CONTEXT_HEADER)
    if payload is None:
        return None
    try:
        carrier = temporalio.converter.PayloadConverter.default.from_payloads([payload])[0]
        if not isinstance(carrier, dict):
            return None
        # HTTPPropagator.extract predates type annotations but returns a Context.
        return cast(Context, HTTPPropagator.extract(carrier))  # type: ignore[no-untyped-call]
    except Exception:
        log.debug("Failed to extract trace context from Temporal headers", exc_info=True)
        return None


def _client_event(event_type: type[TracingEvent], resource: str, tags: dict[str, str]) -> TracingEvent:
    return event_type(
        component=config.temporalio.integration_name,
        integration_config=config.temporalio,
        service=_service(),
        resource=resource,
        tags={**_BASE_TAGS, **tags},
    )


# Temporal is an optional dependency, so its exported interceptor bases are Any in the repository typing environment.
class _DatadogClientOutboundInterceptor(temporalio.client.OutboundInterceptor):  # type: ignore[misc]
    async def start_workflow(self, input_data: Any) -> Any:
        if not _enabled():
            return await self.next.start_workflow(input_data)
        event = _client_event(
            TemporalStartWorkflowEvent,
            input_data.workflow,
            {"temporal.workflow.type": input_data.workflow},
        )
        with core.context_with_event(event) as ctx:
            span = span_from_context(ctx)
            if span is not None:
                _inject_context(input_data, span)
            return await self.next.start_workflow(input_data)

    async def signal_workflow(self, input_data: Any) -> Any:
        if not _enabled():
            return await self.next.signal_workflow(input_data)
        event = _client_event(TemporalSignalWorkflowEvent, input_data.signal, {})
        with core.context_with_event(event) as ctx:
            span = span_from_context(ctx)
            if span is not None:
                _inject_context(input_data, span)
            return await self.next.signal_workflow(input_data)

    async def query_workflow(self, input_data: Any) -> Any:
        if not _enabled():
            return await self.next.query_workflow(input_data)
        event = _client_event(TemporalQueryWorkflowEvent, input_data.query, {})
        with core.context_with_event(event) as ctx:
            span = span_from_context(ctx)
            if span is not None:
                _inject_context(input_data, span)
            return await self.next.query_workflow(input_data)


class _DatadogActivityInboundInterceptor(temporalio.worker.ActivityInboundInterceptor):  # type: ignore[misc]
    async def execute_activity(self, input_data: Any) -> Any:
        if not _enabled():
            return await self.next.execute_activity(input_data)

        info = temporalio.activity.info()
        tags = {
            **_BASE_TAGS,
            "temporal.activity.attempt": str(info.attempt),
            "temporal.activity.type": info.activity_type,
            "temporal.namespace": info.namespace,
            "temporal.task_queue": info.task_queue,
            "temporal.workflow.type": info.workflow_type,
        }
        event = TemporalRunActivityEvent(
            component=config.temporalio.integration_name,
            integration_config=config.temporalio,
            service=_service(),
            resource=info.activity_type,
            tags=tags,
            distributed_context=_extract_context(input_data.headers),
            use_active_context=False,
        )
        with core.context_with_event(event):
            return await self.next.execute_activity(input_data)


class _DatadogTemporalInterceptor(temporalio.client.Interceptor, temporalio.worker.Interceptor):  # type: ignore[misc]
    """Trace client and activity boundaries without entering replayed workflow code."""

    def intercept_client(
        self, next_interceptor: temporalio.client.OutboundInterceptor
    ) -> temporalio.client.OutboundInterceptor:
        return _DatadogClientOutboundInterceptor(next_interceptor)

    def intercept_activity(
        self, next_interceptor: temporalio.worker.ActivityInboundInterceptor
    ) -> temporalio.worker.ActivityInboundInterceptor:
        return _DatadogActivityInboundInterceptor(next_interceptor)


def _traced_client_init(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    if not _enabled():
        return wrapped(*args, **kwargs)

    interceptors = list(kwargs.get("interceptors", ()))
    if not any(isinstance(interceptor, _DatadogTemporalInterceptor) for interceptor in interceptors):
        kwargs = {**kwargs, "interceptors": [_DatadogTemporalInterceptor(), *interceptors]}
    return wrapped(*args, **kwargs)


def patch() -> None:
    if getattr(temporalio, "_datadog_patch", False):
        return
    temporalio._datadog_patch = True
    wrap("temporalio.client", "Client.__init__", _traced_client_init)


def unpatch() -> None:
    if not getattr(temporalio, "_datadog_patch", False):
        return
    temporalio._datadog_patch = False
    unwrap(temporalio.client.Client, "__init__")
