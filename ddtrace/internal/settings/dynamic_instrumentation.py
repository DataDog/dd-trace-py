import os
from pathlib import Path
import re
import typing as t

from ddtrace import config as ddconfig
from ddtrace.internal import gitmetadata
from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.utils.config import get_application_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.version import __version__


AGENTLESS_LOGS_INTAKE_HOST_PREFIX = "http-intake.logs"


DEFAULT_GLOBAL_RATE_LIMIT = 100.0


def _derive_tags(c: DDConfig) -> str:
    _tags = dict(env=ddconfig.env, version=ddconfig.version, debugger_version=__version__)
    _tags.update(ddconfig.tags)

    # Add git metadata tags, if available
    gitmetadata.add_tags(_tags)

    return ",".join([":".join((k, v)) for (k, v) in _tags.items() if v is not None])


def normalize_ident(ident):
    return ident.strip().lower().replace("_", "")


def validate_type_patterns(types: set[str]):
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
    # In agentless CI mode with test replay enabled, DI logs go directly to the logs intake.
    # DD_TEST_FAILED_TEST_REPLAY_ENABLED controls test DI (defaults to enabled)
    _is_agentless = DDConfig.d(
        bool,
        lambda _: (
            asbool(os.getenv("DD_CIVISIBILITY_AGENTLESS_ENABLED", "false"))
            and asbool(os.getenv("DD_TEST_FAILED_TEST_REPLAY_ENABLED", "true"))
        ),
    )
    _intake_url = DDConfig.d(
        str,
        lambda _: (
            "https://{}.{}".format(
                AGENTLESS_LOGS_INTAKE_HOST_PREFIX,
                os.getenv("DD_SITE", "datadoghq.com"),
            )
            if (
                asbool(os.getenv("DD_CIVISIBILITY_AGENTLESS_ENABLED", "false"))
                and asbool(os.getenv("DD_TEST_FAILED_TEST_REPLAY_ENABLED", "true"))
            )
            else agent_config.trace_agent_url
        ),
    )
    _api_key = DDConfig.d(str, lambda _: os.getenv("_CI_DD_API_KEY", os.getenv("DD_API_KEY", "")))
    global_rate_limit = DDConfig.d(float, lambda _: DEFAULT_GLOBAL_RATE_LIMIT)
    _tags_in_qs = DDConfig.d(bool, lambda _: True)
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

    upload_interval_seconds = DDConfig.v(
        float,
        "upload.interval_seconds",
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
        lambda c: (
            re.compile(f"^(?:{'|'.join((_.replace('.', '[.]').replace('*', '.*') for _ in c.redacted_types))})$")
            if c.redacted_types
            else None
        ),
    )

    redaction_excluded_identifiers = DDConfig.v(
        set,
        "redaction_excluded_identifiers",
        map=normalize_ident,
        default=set(),
        help_type="List",
        help="List of identifiers to exclude from redaction",
    )

    probe_file = DDConfig.v(
        t.Optional[Path],
        "probe_file",
        default=None,
        help_type="Path",
        help="Path to a file containing probe definitions",
    )


config = DynamicInstrumentationConfig()
