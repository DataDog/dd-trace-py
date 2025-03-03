import time
from typing import Optional

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.llmobs._constants import PARENT_ID_KEY
from ddtrace.llmobs._constants import ROOT_PARENT_ID
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.trace import Span


def record_llmobs_enabled(error, agentless_enabled, site, start_ts):
    tags = [("agentless", agentless_enabled), ("site", site)]
    if error:
        tags.extend([("error", True), ("error_msg", error)])
    init_time = time.time_ns() - start_ts
    telemetry_writer.add_distribution_metric(
        namespace=TELEMETRY_NAMESPACE.MLOBS, name="llmobs.init_time", value=init_time
    )
    telemetry_writer.add_count_metric(
        namespace=TELEMETRY_NAMESPACE.MLOBS, name="llmobs.enabled", value=1, tags=tuple(tags)
    )


def record_span_start(
    span: Span,
    autoinstrumented: bool,
    decorator: bool = False,
    span_kind: Optional[str] = None,
    integration: Optional[str] = None,
    model_provider: Optional[str] = None,
):
    is_root_span = span._get_ctx_item(PARENT_ID_KEY) != ROOT_PARENT_ID
    has_session_id = span._get_ctx_item(SESSION_ID) is not None
    tags = [("autoinstrumented", autoinstrumented, ("has_session_id", has_session_id), ("is_root_span", is_root_span))]
    if autoinstrumented is False:
        tags.append(("decorator", decorator))
    if span_kind:
        tags.append(("span_kind", span_kind))
    if integration:
        tags.append(("integration", integration))
    if model_provider:
        tags.append(("model_provider", model_provider))
    telemetry_writer.add_count_metric(namespace=TELEMETRY_NAMESPACE.MLOBS, name="span.start", value=1, tags=tuple(tags))
