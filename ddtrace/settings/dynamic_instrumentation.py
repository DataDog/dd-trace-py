from envier import En

from ddtrace import config
from ddtrace.internal.agent import get_trace_url
from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.internal.utils.config import get_application_name
from ddtrace.version import get_version


DEFAULT_MAX_PROBES = 100
DEFAULT_GLOBAL_RATE_LIMIT = 100.0


def _derive_tags(c):
    # type: (En) -> str
    _tags = dict(env=config.env, version=config.version, debugger_version=get_version())
    _tags.update(config.tags)

    return ",".join([":".join((k, v)) for (k, v) in _tags.items() if v is not None])


class DynamicInstrumentationConfig(En):
    __prefix__ = "dd.dynamic_instrumentation"

    service_name = En.d(str, lambda _: config.service or get_application_name() or DEFAULT_SERVICE_NAME)
    _snapshot_intake_url = En.d(str, lambda _: get_trace_url())
    max_probes = En.d(int, lambda _: DEFAULT_MAX_PROBES)
    global_rate_limit = En.d(float, lambda _: DEFAULT_GLOBAL_RATE_LIMIT)
    _tags_in_qs = En.d(bool, lambda _: True)
    _snapshot_intake_endpoint = En.d(str, lambda _: "/debugger/v1/input")
    tags = En.d(str, _derive_tags)

    enabled = En.v(
        bool,
        "enabled",
        default=False,
        help_type="Boolean",
        help="Enable the debugger",
    )

    metrics = En.v(
        bool,
        "metrics.enabled",
        default=True,
        help_type="Boolean",
        help="Enable diagnostic metrics",
    )

    max_payload_size = En.v(
        int,
        "max_payload_size",
        default=1 << 20,  # 1 MB
        help_type="Integer",
        help="Maximum size in bytes of a single configuration payload that can be handled per request",
    )

    upload_timeout = En.v(
        int,
        "upload.timeout",
        default=30,  # seconds
        help_type="Integer",
        help="Timeout in seconds for uploading a snapshot",
    )

    upload_flush_interval = En.v(
        float,
        "upload.flush_interval",
        default=1.0,  # seconds
        help_type="Float",
        help="Interval in seconds for flushing the snapshot upload queue.",
    )

    diagnostics_interval = En.v(
        int,
        "diagnostics.interval",
        default=3600,  # 1 hour
        help_type="Integer",
        help="Interval in seconds for periodically sending probe diagnostic messages",
    )
