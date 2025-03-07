import time

from ddtrace.constants import ERROR_TYPE
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.llmobs._constants import DECORATOR
from ddtrace.llmobs._constants import INTEGRATION
from ddtrace.llmobs._constants import PARENT_ID_KEY
from ddtrace.llmobs._constants import ROOT_PARENT_ID
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.trace import Span


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


def record_span_started():
    telemetry_writer.add_count_metric(namespace=TELEMETRY_NAMESPACE.MLOBS, name="span.start", value=1)


def record_span_created(span: Span):
    is_root_span = span._get_ctx_item(PARENT_ID_KEY) != ROOT_PARENT_ID
    has_session_id = span._get_ctx_item(SESSION_ID) is not None
    integration = span._get_ctx_item(INTEGRATION)
    autoinstrumented = integration is not None
    decorator = span._get_ctx_item(DECORATOR) is True
    span_kind = span._get_ctx_item(SPAN_KIND)
    model_provider = span._get_ctx_item("model_provider")
    status = "ok" if span.error == 0 else "error_{}".format(span.get_tag(ERROR_TYPE))

    tags = [
        ("autoinstrumented", str(autoinstrumented)),
        ("has_session_id", str(has_session_id)),
        ("is_root_span", str(is_root_span)),
        ("span_kind", span_kind or "N/A"),
        ("status", status),
    ]
    if autoinstrumented:
        tags.append(("integration", integration))
    else:
        tags.append(("decorator", str(decorator)))
    if model_provider:
        tags.append(("model_provider", model_provider))
    telemetry_writer.add_count_metric(
        namespace=TELEMETRY_NAMESPACE.MLOBS, name="span.finished", value=1, tags=tuple(tags)
    )
