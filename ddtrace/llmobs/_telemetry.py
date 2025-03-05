import time

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.llmobs._writer import LLMObsSpanEvent


def _find_integration_from_tags(tags):
    integration_tag = next((tag for tag in tags if tag.startswith("integration:")), None)
    if not integration_tag:
        return None
    return integration_tag.split("integration:")[-1]


def record_llmobs_enabled(error, agentless_enabled, site, start_ns):
    tags = [("agentless", agentless_enabled), ("site", site)]
    if error:
        tags.extend([("error", "1"), ("error_type", error)])
    init_time_ms = (time.time_ns() - start_ns) / 1e6
    telemetry_writer.add_distribution_metric(
        namespace=TELEMETRY_NAMESPACE.MLOBS, name="llmobs.init_time", value=init_time_ms, tags=tuple(tags)
    )
    telemetry_writer.add_count_metric(
        namespace=TELEMETRY_NAMESPACE.MLOBS, name="llmobs.enabled", value=1, tags=tuple(tags)
    )


def record_span_truncated(event: LLMObsSpanEvent):
    span_kind = event["meta"].get("span.kind", "")
    integration = _find_integration_from_tags(event["tags"])
    autoinstrumented = 1 if integration is not None else 0
    error = 1 if event["status"] == "error" else 0
    tags = [
        ("span_kind", span_kind),
        ("autoinstrumented", str(autoinstrumented)),
        ("error", str(error)),
     ]
    if integration:
        tags.append(("integration", integration))
    telemetry_writer.add_count_metric(
        namespace=TELEMETRY_NAMESPACE.MLOBS, name="span.truncated", value=1, tags=tuple(tags)
    )


def record_span_event_size(event: LLMObsSpanEvent, event_size: int):
    span_kind = event["meta"].get("span.kind", "")
    integration = _find_integration_from_tags(event["tags"])
    autoinstrumented = 1 if integration is not None else 0
    error = 1 if event["status"] == "error" else 0
    tags = [
        ("span_kind", span_kind),
        ("autoinstrumented", str(autoinstrumented)),
        ("error", str(error)),
     ]
    if integration:
        tags.append(("integration", integration))
    telemetry_writer.add_distribution_metric(
        namespace=TELEMETRY_NAMESPACE.MLOBS, name="span.size", value=event_size, tags=tuple(tags)
    )
