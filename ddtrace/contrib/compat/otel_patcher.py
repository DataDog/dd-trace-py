# ddtrace/contrib/compat/otel_patcher.py
"""
Monkey-patches OpenTelemetry Span classes to support Datadog's span interface.

This module should be imported early (e.g., in sitecustomize or auto.py) when
EXPERIMENTAL_OTEL_DD_INSTRUMENTATION_ENABLED=true. After patching, all OTel
spans natively support DD methods like _set_tag_str(), set_metric(), error property, etc.

No re-exports needed - all code uses the original trace_utils unchanged.
"""

from typing import Any
from typing import Union

from opentelemetry.trace import Span as OtelSpan
from opentelemetry.trace.status import Status
from opentelemetry.trace.status import StatusCode

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _patch_otel_span_class():
    """
    Patch OpenTelemetry Span class to add Datadog-compatible methods.
    Called once at startup when OTel DD instrumentation is enabled.
    """

    def _set_tag_str(self: OtelSpan, key: str, value: Any) -> None:
        """
        Datadog's _set_tag_str -> OTel's set_attribute.
        Used for string tags.
        """
        if value is not None:
            self.set_attribute(key, str(value))

    def set_tag(self: OtelSpan, key: str, value: Any) -> None:
        """
        Datadog's set_tag -> OTel's set_attribute.
        Used for generic tags (string, bool, etc).
        """
        if value is not None:
            self.set_attribute(key, value)

    def set_metric(self: OtelSpan, key: str, value: Union[int, float]) -> None:
        """
        Datadog's set_metric -> OTel's set_attribute.
        Used for numeric metrics. OTel's set_attribute handles both strings and numbers.
        """
        if value is not None:
            self.set_attribute(key, value)

    def get_tag(self: OtelSpan, key: str) -> None:
        """
        Datadog's get_tag (OTel doesn't support reading attributes after setting).
        Returns None to maintain compatibility.
        """
        log.debug("get_tag() on OTel span returns None (OTel doesn't support reading attributes)")
        return None

    def get_metric(self: OtelSpan, key: str) -> None:
        """
        Datadog's get_metric (OTel doesn't support reading attributes after setting).
        Returns None to maintain compatibility.
        """
        log.debug("get_metric() on OTel span returns None (OTel doesn't support reading attributes)")
        return None

    def _get_error(self: OtelSpan) -> int:
        """
        Datadog's error property getter.
        Returns 1 if error was set, 0 otherwise.
        """
        return 1 if getattr(self, "_dd_error_set", False) else 0

    def _set_error(self: OtelSpan, value: int) -> None:
        """
        Datadog's error property setter -> OTel's set_status.
        Maps error=1 to ERROR status, error=0 to OK status.
        """
        if value:
            self.set_status(Status(StatusCode.ERROR))
            self._dd_error_set = True
        else:
            self.set_status(Status(StatusCode.OK))
            self._dd_error_set = False

    # Apply patches to OtelSpan class
    OtelSpan._set_tag_str = _set_tag_str  # type: ignore[assignment]
    OtelSpan.set_tag = set_tag  # type: ignore[method-assign]
    OtelSpan.set_metric = set_metric  # type: ignore[method-assign]
    OtelSpan.get_tag = get_tag  # type: ignore[method-assign]
    OtelSpan.get_metric = get_metric  # type: ignore[method-assign]
    OtelSpan.error = property(_get_error, _set_error)  # type: ignore[assignment]

    log.debug("OpenTelemetry Span class patched with Datadog-compatible methods")


# Patch immediately when module is imported
_patch_otel_span_class()
