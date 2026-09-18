"""Span finish callback types, kept here to preserve their existing import paths."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FinishContext:
    """Context passed to a user-supplied ``on_span_finish`` callback.

    Attributes:
        operation: The Temporal operation name (e.g. ``"RunWorkflow"``).
        exception: The exception that caused the span to fail, or ``None``.
    """

    operation: str
    exception: BaseException | None


@dataclass(frozen=True)
class FinishResult:
    """Returned by ``on_span_finish`` to control how a span is finished.

    All fields default to leaving the interceptor's default behavior in place;
    return ``None`` from the callback (or omit the callback) for the default.

    Attributes:
        extra_tags: Tags applied to the span before it is finished.
    """

    extra_tags: Mapping[str, Any] | None = None
