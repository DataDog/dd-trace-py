"""Span annotation logic for the Datadog tracing interceptor."""

from collections.abc import Mapping
from typing import Any

from ddtrace._trace.span import Span
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.internal.constants import COMPONENT

from .constants import _MANUAL_KEEP_OPS
from .constants import COMPONENT_NAME
from .constants import TEMPORAL_TAG_PREFIX
from .constants import OperationNames


class _SpanAnnotator:
    _PEER_SERVICE_TAG = "peer.service"

    _SPAN_KIND: dict[str, str] = {
        OperationNames.START_ACTIVITY: SpanKind.PRODUCER,
        OperationNames.RUN_ACTIVITY: SpanKind.CONSUMER,
        OperationNames.START_CHILD_WORKFLOW: SpanKind.PRODUCER,
        OperationNames.START_WORKFLOW: SpanKind.PRODUCER,
        OperationNames.SIGNAL_WITH_START_WORKFLOW: SpanKind.PRODUCER,
        OperationNames.RUN_WORKFLOW: SpanKind.CONSUMER,
        OperationNames.SIGNAL_WORKFLOW: SpanKind.PRODUCER,
        OperationNames.SIGNAL_CHILD_WORKFLOW: SpanKind.PRODUCER,
        OperationNames.SIGNAL_EXTERNAL_WORKFLOW: SpanKind.PRODUCER,
        OperationNames.HANDLE_SIGNAL: SpanKind.CONSUMER,
        OperationNames.QUERY_WORKFLOW: SpanKind.PRODUCER,
        OperationNames.HANDLE_QUERY: SpanKind.CONSUMER,
        OperationNames.UPDATE_WORKFLOW: SpanKind.PRODUCER,
        OperationNames.UPDATE_WITH_START_WORKFLOW: SpanKind.PRODUCER,
        OperationNames.VALIDATE_UPDATE: SpanKind.CONSUMER,
        OperationNames.HANDLE_UPDATE: SpanKind.CONSUMER,
        OperationNames.CREATE_SCHEDULE: SpanKind.PRODUCER,
        OperationNames.START_NEXUS_OPERATION: SpanKind.PRODUCER,
        OperationNames.RUN_NEXUS_OPERATION_START_HANDLER: SpanKind.CONSUMER,
        OperationNames.RUN_NEXUS_OPERATION_CANCEL_HANDLER: SpanKind.CONSUMER,
    }

    def __init__(
        self,
        *,
        service_name: str | None = None,
        extra_tags: Mapping[str, str] | None = None,
    ) -> None:
        self.service_name = service_name
        self.extra_tags: Mapping[str, str] = extra_tags or {}

    @classmethod
    def _normalize_key(cls, key: str) -> str:
        if key.startswith(TEMPORAL_TAG_PREFIX):
            return key
        if key.lower().startswith("temporal"):
            return TEMPORAL_TAG_PREFIX + key[len("temporal") :].lstrip(".")
        return TEMPORAL_TAG_PREFIX + key

    def annotate(
        self,
        span: Span,
        operation: str,
        attributes: Mapping[str, Any] | None,
        parent_service_name: str | None,
        force_keep: bool = False,
    ) -> None:
        # User-defined global custom tags
        span.set_tags(dict(self.extra_tags))

        # Mark every span as coming from the temporal integration so the tracer
        # attributes integration telemetry (spans_created/finished, etc.) to
        # temporal instead of the generic datadog span API.
        span.set_tag(COMPONENT, COMPONENT_NAME)

        # Attributes from the operation
        if attributes:
            for key, value in attributes.items():
                span.set_tag(self._normalize_key(key), value)

        # Force-keep entry-point operations that have no local parent.
        # Two parent shapes qualify: no parent (nil/None — scheduled or standalone
        # execution) and parents extracted from Temporal task headers (cross-process).
        # Parents from context_provider.active() are in-process producer spans and
        # should inherit the caller's sampling decision instead.
        if operation in _MANUAL_KEEP_OPS and force_keep:
            span.set_tag(MANUAL_KEEP_KEY)

        kind = self._SPAN_KIND.get(operation)
        if kind:
            span.set_tag(SPAN_KIND, kind)
            if kind == SpanKind.CONSUMER and parent_service_name and parent_service_name != self.service_name:
                span.set_tag(self._PEER_SERVICE_TAG, parent_service_name)
