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
    """

    def _set_tag_str(self: OtelSpan, key: str, value: str) -> None:
        if value is not None:
            self.set_attribute(key, value)

    def set_tag(self: OtelSpan, key: str, value: Any) -> None:
        if value is not None:
            self.set_attribute(key, value)

    def set_metric(self: OtelSpan, key: str, value: Union[int, float]) -> None:
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
        return 1 if getattr(self, "_dd_error_set", False) else 0

    def _set_error(self: OtelSpan, value: int) -> None:
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


_patch_otel_span_class()
