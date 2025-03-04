import time

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


def record_llmobs_enabled(error, agentless_enabled, site, start_ns):
    tags = [("agentless", agentless_enabled), ("site", site), ("status", "error_{}".format(error) if error else "ok")]
    init_time_ms = (time.time_ns() - start_ns) / 1e6
    telemetry_writer.add_distribution_metric(
        namespace=TELEMETRY_NAMESPACE.MLOBS, name="llmobs.init_time", value=init_time_ms, tags=tuple(tags)
    )
    telemetry_writer.add_count_metric(
        namespace=TELEMETRY_NAMESPACE.MLOBS, name="llmobs.enabled", value=1, tags=tuple(tags)
    )
