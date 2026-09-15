from collections.abc import Callable
from typing import Any
from typing import cast
from urllib.parse import urlsplit

import temporalio
import temporalio.activity
import temporalio.client
import temporalio.converter
import temporalio.worker

from ddtrace import config
from ddtrace.contrib._events.temporalio import TemporalContextForwardEvent
from ddtrace.contrib._events.temporalio import TemporalEvent
from ddtrace.contrib._events.temporalio import TemporalHeadersDecodeEvent
from ddtrace.contrib._events.temporalio import TemporalQueryWorkflowEvent
from ddtrace.contrib._events.temporalio import TemporalRunActivityEvent
from ddtrace.contrib._events.temporalio import TemporalSignalWorkflowEvent
from ddtrace.contrib._events.temporalio import TemporalStartWorkflowEvent
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_OPERATION
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

_BASE_TAGS = {"messaging.system": "temporal"}


def get_version() -> str:
    return str(getattr(temporalio, "__version__", ""))


def _supported_versions() -> dict[str, str]:
    return {"temporalio": ">=1.0.0"}


def _enabled() -> bool:
    return getattr(temporalio, "_datadog_patch", False) and config.temporalio.get("enabled") is not False


def _client_event(
    event_type: type[TemporalEvent],
    input_data: Any,
    payload_converter: Any,
    resource: str,
    tags: dict[str, str],
) -> TemporalEvent:
    return event_type(
        component=config.temporalio.integration_name,
        integration_config=config.temporalio,
        input_data=input_data,
        payload_converter=payload_converter,
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
    def __init__(self, next_interceptor: Any, root: "_DatadogTemporalInterceptor") -> None:
        super().__init__(next_interceptor)
        self._root = root

    async def start_workflow(self, input_data: Any) -> Any:
        if not _enabled():
            return await self.next.start_workflow(input_data)
        event = _client_event(
            TemporalStartWorkflowEvent,
            input_data,
            self._root.payload_converter,
            input_data.workflow,
            {
                **self._root.peer_tags,
                MESSAGING_DESTINATION_NAME: input_data.task_queue,
                MESSAGING_OPERATION: "send",
                "temporal.namespace": self._root.namespace,
                "temporal.task_queue": input_data.task_queue,
                "temporal.workflow.type": input_data.workflow,
            },
        )
        with core.context_with_event(event):
            return await self.next.start_workflow(input_data)

    async def signal_workflow(self, input_data: Any) -> Any:
        if not _enabled():
            return await self.next.signal_workflow(input_data)
        event = _client_event(
            TemporalSignalWorkflowEvent,
            input_data,
            self._root.payload_converter,
            input_data.signal,
            {**self._root.peer_tags, MESSAGING_OPERATION: "send", "temporal.namespace": self._root.namespace},
        )
        with core.context_with_event(event):
            return await self.next.signal_workflow(input_data)

    async def query_workflow(self, input_data: Any) -> Any:
        if not _enabled():
            return await self.next.query_workflow(input_data)
        event = _client_event(
            TemporalQueryWorkflowEvent,
            input_data,
            self._root.payload_converter,
            input_data.query,
            {**self._root.peer_tags, "temporal.namespace": self._root.namespace},
        )
        with core.context_with_event(event):
            return await self.next.query_workflow(input_data)


class _DatadogActivityInboundInterceptor(temporalio.worker.ActivityInboundInterceptor):  # type: ignore[misc]
    def __init__(self, next_interceptor: Any, root: "_DatadogTemporalInterceptor") -> None:
        super().__init__(next_interceptor)
        self._root = root

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
        headers_event = TemporalHeadersDecodeEvent(
            input_data=input_data,
            payload_converter=self._root.payload_converter,
        )
        core.dispatch_event(headers_event)
        event = TemporalRunActivityEvent(
            component=config.temporalio.integration_name,
            integration_config=config.temporalio,
            input_data=input_data,
            payload_converter=self._root.payload_converter,
            request_headers=headers_event.request_headers,
            resource=info.activity_type,
            tags=tags,
            activate_distributed_headers=True,
        )
        with core.context_with_event(event):
            return await self.next.execute_activity(input_data)


class _DatadogWorkflowInboundInterceptor(temporalio.worker.WorkflowInboundInterceptor):  # type: ignore[misc]
    """Collect workflow boundary data without instrumenting replayed workflow code."""

    def __init__(self, next_interceptor: Any) -> None:
        super().__init__(next_interceptor)
        self._input_data: Any = None

    def init(self, outbound: Any) -> None:
        super().init(_DatadogWorkflowOutboundInterceptor(outbound, self))

    async def execute_workflow(self, input_data: Any) -> Any:
        self._input_data = input_data
        return await self.next.execute_workflow(input_data)

    def collect_outbound(self, input_data: Any) -> None:
        if _enabled() and self._input_data is not None:
            core.dispatch_event(
                TemporalContextForwardEvent(
                    source_input_data=self._input_data,
                    destination_input_data=input_data,
                )
            )


class _DatadogWorkflowOutboundInterceptor(temporalio.worker.WorkflowOutboundInterceptor):  # type: ignore[misc]
    def __init__(self, next_interceptor: Any, root: _DatadogWorkflowInboundInterceptor) -> None:
        super().__init__(next_interceptor)
        self._root = root

    def _collect(self, input_data: Any) -> None:
        self._root.collect_outbound(input_data)

    def continue_as_new(self, input_data: Any) -> Any:
        self._collect(input_data)
        return self.next.continue_as_new(input_data)

    async def signal_child_workflow(self, input_data: Any) -> Any:
        self._collect(input_data)
        return await self.next.signal_child_workflow(input_data)

    async def signal_external_workflow(self, input_data: Any) -> Any:
        self._collect(input_data)
        return await self.next.signal_external_workflow(input_data)

    def start_activity(self, input_data: Any) -> Any:
        self._collect(input_data)
        return self.next.start_activity(input_data)

    async def start_child_workflow(self, input_data: Any) -> Any:
        self._collect(input_data)
        return await self.next.start_child_workflow(input_data)

    def start_local_activity(self, input_data: Any) -> Any:
        self._collect(input_data)
        return self.next.start_local_activity(input_data)


class _DatadogTemporalInterceptor(temporalio.client.Interceptor, temporalio.worker.Interceptor):  # type: ignore[misc]
    """Collect client and activity operation data without instrumenting replayed workflow code."""

    def __init__(self, namespace: str, peer_tags: dict[str, str], payload_converter: Any) -> None:
        self._namespace = namespace
        self._peer_tags = peer_tags
        self._payload_converter = payload_converter

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def peer_tags(self) -> dict[str, str]:
        return self._peer_tags

    @property
    def payload_converter(self) -> Any:
        return self._payload_converter

    def update_payload_converter(self, payload_converter: Any) -> None:
        self._payload_converter = payload_converter

    def intercept_client(
        self, next_interceptor: temporalio.client.OutboundInterceptor
    ) -> temporalio.client.OutboundInterceptor:
        return _DatadogClientOutboundInterceptor(next_interceptor, self)

    def intercept_activity(
        self, next_interceptor: temporalio.worker.ActivityInboundInterceptor
    ) -> temporalio.worker.ActivityInboundInterceptor:
        return _DatadogActivityInboundInterceptor(next_interceptor, self)

    def workflow_interceptor_class(self, input_data: Any) -> type[_DatadogWorkflowInboundInterceptor]:
        return _DatadogWorkflowInboundInterceptor


def _intercepted_client_init(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    if not _enabled():
        return wrapped(*args, **kwargs)

    interceptors = list(kwargs.get("interceptors", ()))
    datadog_interceptor = next(
        (interceptor for interceptor in interceptors if isinstance(interceptor, _DatadogTemporalInterceptor)), None
    )
    if datadog_interceptor is None:
        service_client = args[0] if args else kwargs.get("service_client")
        namespace = cast(str, kwargs.get("namespace", "default"))
        data_converter = kwargs.get("data_converter", temporalio.converter.DataConverter.default)
        datadog_interceptor = _DatadogTemporalInterceptor(
            namespace, _peer_tags(service_client), data_converter.payload_converter
        )
        kwargs = {
            **kwargs,
            "interceptors": [datadog_interceptor, *interceptors],
        }
    result = wrapped(*args, **kwargs)
    datadog_interceptor.update_payload_converter(instance.data_converter.payload_converter)
    return result


def patch() -> None:
    if getattr(temporalio, "_datadog_patch", False):
        return
    temporalio._datadog_patch = True
    wrap("temporalio.client", "Client.__init__", _intercepted_client_init)


def unpatch() -> None:
    if not getattr(temporalio, "_datadog_patch", False):
        return
    temporalio._datadog_patch = False
    unwrap(temporalio.client.Client, "__init__")
