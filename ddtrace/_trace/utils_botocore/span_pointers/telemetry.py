from ddtrace.internal.telemetry import telemetry_writer


def record_span_pointer_calculation(span_pointer_count: int) -> None:
    telemetry_writer.add_count_metric(
        namespace="tracer",
        name="botocore_span_pointer_calculation",
        value=1,
        tags=None,
    )

    telemetry_writer.add_count_metric(
        namespace="tracer",
        name="botocore_span_pointer_count",
        value=span_pointer_count,
        tags=None,
    )


def record_span_pointer_calcuation_issue(operation: str) -> None:
    telemetry_writer.add_count_metric(
        namespace="tracer",
        name="botocore_span_pointer_calculation_issue",
        value=1,
        tags=(("operation", operation),),
    )
