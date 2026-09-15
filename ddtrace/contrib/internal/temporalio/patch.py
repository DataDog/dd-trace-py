from collections.abc import Callable
from collections.abc import Mapping
from typing import Any
from typing import Optional
from typing import cast
from urllib.parse import urlsplit

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
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_OPERATION
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings._config import _get_config
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils.formats import asbool
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import Span


config._add(
    "temporalio",
    {
        # Schema functions are selected dynamically and are untyped.
        "_default_service": schematize_service_name("temporalio"),  # type: ignore[operator]
        "distributed_tracing": asbool(_get_config("DD_TEMPORALIO_DISTRIBUTED_TRACING", default=True)),
    },
)  # type: ignore[no-untyped-call]


log = get_logger(__name__)

_CONTEXT_HEADER = "_datadog"
_BASE_TAGS = {"messaging.system": "temporal"}


def get_version() -> str:
    return str(getattr(temporalio, "__version__", ""))


def _supported_versions() -> dict[str, str]:
    return {"temporalio": ">=1.0.0"}


def _service() -> Optional[str]:
    return ext_service(None, config.temporalio)


def _enabled() -> bool:
    return config.temporalio.get("enabled") is not False


def _inject_context(input_data: Any, span: Span) -> None:
    if not config.temporalio.distributed_tracing:
        return
    carrier: dict[str, str] = {}
    try:
        HTTPPropagator.inject(span, carrier)
        if carrier:
            payload = temporalio.converter.PayloadConverter.default.to_payloads([carrier])[0]
            input_data.headers = {**input_data.headers, _CONTEXT_HEADER: payload}
    except Exception:
        log.debug("Failed to inject trace context into Temporal headers", exc_info=True)


def _extract_context(headers: Mapping[str, temporalio.api.common.v1.Payload]) -> Optional[Context]:
    if not config.temporalio.distributed_tracing:
        return None
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


def _peer_tags(service_client: Any) -> dict[str, str]:
    target_host = getattr(getattr(service_client, "config", None), "target_host", None)
    if not target_host:
        return {}
    try:
        parsed = urlsplit(target_host if "://" in target_host else f"//{target_host}")
        tags = {"out.host": parsed.hostname} if parsed.hostname else {}
        if parsed.port is not None:
            tags["network.destination.port"] = str(parsed.port)
        return tags
    except ValueError:
        log.debug("Failed to parse Temporal target host", exc_info=True)
        return {}


# Temporal is an optional dependency, so its exported interceptor bases are Any in the repository typing environment.
class _DatadogClientOutboundInterceptor(temporalio.client.OutboundInterceptor):  # type: ignore[misc]
    def __init__(self, next_interceptor: Any, namespace: str, peer_tags: dict[str, str]) -> None:
        super().__init__(next_interceptor)
        self._namespace = namespace
        self._peer_tags = peer_tags

    async def start_workflow(self, input_data: Any) -> Any:
        if not _enabled():
            return await self.next.start_workflow(input_data)
        event = _client_event(
            TemporalStartWorkflowEvent,
            input_data.workflow,
            {
                **self._peer_tags,
                MESSAGING_DESTINATION_NAME: input_data.task_queue,
                MESSAGING_OPERATION: "send",
                "temporal.namespace": self._namespace,
                "temporal.task_queue": input_data.task_queue,
                "temporal.workflow.type": input_data.workflow,
            },
        )
        with core.context_with_event(event) as ctx:
            span = span_from_context(ctx)
            if span is not None:
                _inject_context(input_data, span)
            return await self.next.start_workflow(input_data)

    async def signal_workflow(self, input_data: Any) -> Any:
        if not _enabled():
            return await self.next.signal_workflow(input_data)
        event = _client_event(
            TemporalSignalWorkflowEvent,
            input_data.signal,
            {**self._peer_tags, MESSAGING_OPERATION: "send", "temporal.namespace": self._namespace},
        )
        with core.context_with_event(event) as ctx:
            span = span_from_context(ctx)
            if span is not None:
                _inject_context(input_data, span)
            return await self.next.signal_workflow(input_data)

    async def query_workflow(self, input_data: Any) -> Any:
        if not _enabled():
            return await self.next.query_workflow(input_data)
        event = _client_event(
            TemporalQueryWorkflowEvent,
            input_data.query,
            {**self._peer_tags, "temporal.namespace": self._namespace},
        )
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
            MESSAGING_DESTINATION_NAME: info.task_queue,
            MESSAGING_OPERATION: "process",
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


class _DatadogWorkflowInboundInterceptor(temporalio.worker.WorkflowInboundInterceptor):  # type: ignore[misc]
    """Forward trace headers through replayed workflow code without creating spans there."""

    def __init__(self, next_interceptor: Any) -> None:
        super().__init__(next_interceptor)
        self._context_payload: Optional[temporalio.api.common.v1.Payload] = None

    def init(self, outbound: Any) -> None:
        super().init(_DatadogWorkflowOutboundInterceptor(outbound, self))

    async def execute_workflow(self, input_data: Any) -> Any:
        self._context_payload = input_data.headers.get(_CONTEXT_HEADER)
        return await self.next.execute_workflow(input_data)

    def inject_headers(
        self, headers: Mapping[str, temporalio.api.common.v1.Payload]
    ) -> Mapping[str, temporalio.api.common.v1.Payload]:
        if not config.temporalio.distributed_tracing or self._context_payload is None:
            return headers
        return {**headers, _CONTEXT_HEADER: self._context_payload}


class _DatadogWorkflowOutboundInterceptor(temporalio.worker.WorkflowOutboundInterceptor):  # type: ignore[misc]
    def __init__(self, next_interceptor: Any, root: _DatadogWorkflowInboundInterceptor) -> None:
        super().__init__(next_interceptor)
        self._root = root

    def _inject(self, input_data: Any) -> None:
        input_data.headers = self._root.inject_headers(input_data.headers)

    def continue_as_new(self, input_data: Any) -> Any:
        self._inject(input_data)
        return self.next.continue_as_new(input_data)

    async def signal_child_workflow(self, input_data: Any) -> Any:
        self._inject(input_data)
        return await self.next.signal_child_workflow(input_data)

    async def signal_external_workflow(self, input_data: Any) -> Any:
        self._inject(input_data)
        return await self.next.signal_external_workflow(input_data)

    def start_activity(self, input_data: Any) -> Any:
        self._inject(input_data)
        return self.next.start_activity(input_data)

    async def start_child_workflow(self, input_data: Any) -> Any:
        self._inject(input_data)
        return await self.next.start_child_workflow(input_data)

    def start_local_activity(self, input_data: Any) -> Any:
        self._inject(input_data)
        return self.next.start_local_activity(input_data)


class _DatadogTemporalInterceptor(temporalio.client.Interceptor, temporalio.worker.Interceptor):  # type: ignore[misc]
    """Trace client and activity boundaries without entering replayed workflow code."""

    def __init__(self, namespace: str, peer_tags: dict[str, str]) -> None:
        self._namespace = namespace
        self._peer_tags = peer_tags

    def intercept_client(
        self, next_interceptor: temporalio.client.OutboundInterceptor
    ) -> temporalio.client.OutboundInterceptor:
        return _DatadogClientOutboundInterceptor(next_interceptor, self._namespace, self._peer_tags)

    def intercept_activity(
        self, next_interceptor: temporalio.worker.ActivityInboundInterceptor
    ) -> temporalio.worker.ActivityInboundInterceptor:
        return _DatadogActivityInboundInterceptor(next_interceptor)

    def workflow_interceptor_class(self, input_data: Any) -> type[_DatadogWorkflowInboundInterceptor]:
        return _DatadogWorkflowInboundInterceptor


def _traced_client_init(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    if not _enabled():
        return wrapped(*args, **kwargs)

    interceptors = list(kwargs.get("interceptors", ()))
    if not any(isinstance(interceptor, _DatadogTemporalInterceptor) for interceptor in interceptors):
        service_client = args[0] if args else kwargs.get("service_client")
        namespace = cast(str, kwargs.get("namespace", "default"))
        kwargs = {
            **kwargs,
            "interceptors": [_DatadogTemporalInterceptor(namespace, _peer_tags(service_client)), *interceptors],
        }
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
