from typing import Optional
from typing import Tuple

from ddtrace.internal.telemetry import telemetry_writer


def record_span_pointer_calculation(context: str, span_pointer_count: int) -> None:
    telemetry_writer.add_count_metric(
        namespace="tracer", name="span_pointer_calculation", value=1, tags=(("context", context),)
    )

    telemetry_writer.add_count_metric(
        namespace="tracer", name="span_pointer_calculated_count", value=span_pointer_count, tags=(("context", context),)
    )


def record_span_pointer_calculation_issue(
    context: str, additional_tags: Optional[Tuple[Tuple[str, str], ...]] = None
) -> None:
    tags: Tuple[Tuple[str, str], ...] = (("context", context),)
    if additional_tags:
        tags += additional_tags

    telemetry_writer.add_count_metric(
        namespace="tracer",
        name="span_pointer_calculation_issue",
        value=1,
        tags=tags,
    )
