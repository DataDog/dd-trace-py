import re
import typing as t

from ddtrace import config as ddconfig
from ddtrace.internal import gitmetadata
from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.internal.utils.config import get_application_name
from ddtrace.settings._agent import config as agent_config
from ddtrace.settings._core import DDConfig
from ddtrace.version import get_version


DEFAULT_MAX_PROBES = 100
DEFAULT_GLOBAL_RATE_LIMIT = 100.0


def _derive_tags(c):
    # type: (DDConfig) -> str
    _tags = dict(env=ddconfig.env, version=ddconfig.version, debugger_version=get_version())
    _tags.update(ddconfig.tags)

    # Add git metadata tags, if available
    gitmetadata.add_tags(_tags)

    return ",".join([":".join((k, v)) for (k, v) in _tags.items() if v is not None])


def normalize_ident(ident):
    return ident.strip().lower().replace("_", "")


def validate_type_patterns(types: t.Set[str]):
    for typ in types:
        for s in typ.strip().split("."):
            s = s.strip().replace("*", "a")
            if not (
                s.isidentifier()
                or s
                in {
                    "<locals>",
                }
            ):
                raise ValueError(f"Invalid redaction type pattern {typ}: {s} is not a valid identifier")


class DynamicInstrumentationConfig(DDConfig):
    __prefix__ = "dd.dynamic_instrumentation"

    service_name = DDConfig.d(str, lambda _: ddconfig.service or get_application_name() or DEFAULT_SERVICE_NAME)
    _intake_url = DDConfig.d(str, lambda _: agent_config.trace_agent_url)
    max_probes = DDConfig.d(int, lambda _: DEFAULT_MAX_PROBES)
    global_rate_limit = DDConfig.d(float, lambda _: DEFAULT_GLOBAL_RATE_LIMIT)
    _tags_in_qs = DDConfig.d(bool, lambda _: True)
    _intake_endpoint = DDConfig.d(str, lambda _: "/debugger/v1/input")
    tags = DDConfig.d(str, _derive_tags)

    enabled = DDConfig.v(
        bool,
        "enabled",
        default=False,
        help_type="Boolean",
        help="Enable Dynamic Instrumentation",
    )

    metrics = DDConfig.v(
        bool,
        "metrics.enabled",
        default=True,
        help_type="Boolean",
        help="Enable Dynamic Instrumentation diagnostic metrics",
    )

    max_payload_size = DDConfig.v(
        int,
        "max_payload_size",
        default=1 << 20,  # 1 MB
        help_type="Integer",
        help="Maximum size in bytes of a single configuration payload that can be handled per request",
    )

    upload_timeout = DDConfig.v(
        int,
        "upload.timeout",
        default=30,  # seconds
        help_type="Integer",
        help="Timeout in seconds for uploading Dynamic Instrumentation payloads",
    )

    upload_flush_interval = DDConfig.v(
        float,
        "upload.flush_interval",
        default=1.0,  # seconds
        help_type="Float",
        help="Interval in seconds for flushing the dynamic logs upload queue",
    )

    diagnostics_interval = DDConfig.v(
        int,
        "diagnostics.interval",
        default=3600,  # 1 hour
        help_type="Integer",
        help="Interval in seconds for periodically emitting probe diagnostic messages",
    )

    redacted_identifiers = DDConfig.v(
        set,
        "redacted_identifiers",
        map=normalize_ident,
        default=set(),
        help_type="List",
        help="List of identifiers/object attributes/dict keys to redact from dynamic logs and snapshots",
    )

    redacted_types = DDConfig.v(
        set,
        "redacted_types",
        map=str.strip,
        default=set(),
        validator=validate_type_patterns,
        help_type="List",
        help="List of object types to redact from dynamic logs and snapshots",
    )

    redacted_types_re = DDConfig.d(
        t.Optional[re.Pattern],
        lambda c: re.compile(f"^(?:{'|'.join((_.replace('.', '[.]').replace('*', '.*') for _ in c.redacted_types))})$")
        if c.redacted_types
        else None,
    )

    redaction_excluded_identifiers = DDConfig.v(
        set,
        "redaction_excluded_identifiers",
        map=normalize_ident,
        default=set(),
        help_type="List",
        help="List of identifiers to exclude from redaction",
    )


config = DynamicInstrumentationConfig()
